package bot

import (
	"context"
	"fmt"
	"log/slog"
	"strings"
	"time"

	"discord-mod-bot/internal/database"
	"github.com/bwmarrin/discordgo"
)

type Bot struct {
	session    *discordgo.Session
	store      *database.Store
	guildID    string
	commands   []*discordgo.ApplicationCommand
	registered []*discordgo.ApplicationCommand
}

func New(token, guildID string, store *database.Store) (*Bot, error) {
	s, err := discordgo.New("Bot " + token)
	if err != nil {
		return nil, err
	}
	s.Identify.Intents = discordgo.IntentsGuilds | discordgo.IntentsGuildMembers | discordgo.IntentsGuildMessages | discordgo.IntentsMessageContent
	b := &Bot{session: s, store: store, guildID: guildID}
	b.commands = b.commandDefinitions()
	s.AddHandler(b.onInteraction)
	s.AddHandler(b.onMessageDelete)
	s.AddHandler(b.onMessageUpdate)
	return b, nil
}

func (b *Bot) Open() error {
	if err := b.session.Open(); err != nil {
		return err
	}
	for _, cmd := range b.commands {
		registered, err := b.session.ApplicationCommandCreate(b.session.State.User.ID, b.guildID, cmd)
		if err != nil {
			return fmt.Errorf("register command %s: %w", cmd.Name, err)
		}
		b.registered = append(b.registered, registered)
	}
	slog.Info("discord bot connected", "user", b.session.State.User.String())
	return nil
}

func (b *Bot) Close() error { return b.session.Close() }

func (b *Bot) commandDefinitions() []*discordgo.ApplicationCommand {
	userOpt := func(desc string) *discordgo.ApplicationCommandOption {
		return &discordgo.ApplicationCommandOption{Type: discordgo.ApplicationCommandOptionUser, Name: "user", Description: desc, Required: true}
	}
	reasonOpt := &discordgo.ApplicationCommandOption{Type: discordgo.ApplicationCommandOptionString, Name: "reason", Description: "Reason for the moderation action", Required: false, MaxLength: 500}
	return []*discordgo.ApplicationCommand{
		{Name: "kick", Description: "Kick a member", DefaultMemberPermissions: ptrInt64(discordgo.PermissionKickMembers), Options: []*discordgo.ApplicationCommandOption{userOpt("Member to kick"), reasonOpt}},
		{Name: "ban", Description: "Ban a member", DefaultMemberPermissions: ptrInt64(discordgo.PermissionBanMembers), Options: []*discordgo.ApplicationCommandOption{userOpt("Member to ban"), reasonOpt}},
		{Name: "warn", Description: "Warn a member", DefaultMemberPermissions: ptrInt64(discordgo.PermissionModerateMembers), Options: []*discordgo.ApplicationCommandOption{userOpt("Member to warn"), {Type: discordgo.ApplicationCommandOptionString, Name: "reason", Description: "Reason for the warning", Required: true, MaxLength: 500}}},
		{Name: "warnings", Description: "Show a member's warnings", DefaultMemberPermissions: ptrInt64(discordgo.PermissionModerateMembers), Options: []*discordgo.ApplicationCommandOption{userOpt("Member to inspect")}},
		{Name: "userinfo", Description: "Show information about a member", Options: []*discordgo.ApplicationCommandOption{userOpt("Member to inspect")}},
		{Name: "lock", Description: "Lock the current channel", DefaultMemberPermissions: ptrInt64(discordgo.PermissionManageChannels), Options: []*discordgo.ApplicationCommandOption{reasonOpt}},
		{Name: "unlock", Description: "Unlock the current channel", DefaultMemberPermissions: ptrInt64(discordgo.PermissionManageChannels)},
	}
}

func ptrInt64(v int64) *int64 { return &v }

func (b *Bot) onInteraction(s *discordgo.Session, i *discordgo.InteractionCreate) {
	if i.Type != discordgo.InteractionApplicationCommand {
		return
	}
	cmd := i.ApplicationCommandData()
	switch cmd.Name {
	case "kick":
		b.kick(s, i)
	case "ban":
		b.ban(s, i)
	case "warn":
		b.warn(s, i)
	case "warnings":
		b.warnings(s, i)
	case "userinfo":
		b.userinfo(s, i)
	case "lock":
		b.lock(s, i)
	case "unlock":
		b.unlock(s, i)
	}
}

func optionUser(s *discordgo.Session, data discordgo.ApplicationCommandInteractionData, guildID string) *discordgo.User {
	for _, o := range data.Options {
		if o.Name == "user" {
			return o.UserValue(s)
		}
	}
	return nil
}
func optionString(data discordgo.ApplicationCommandInteractionData, name, fallback string) string {
	for _, o := range data.Options {
		if o.Name == name {
			v := strings.TrimSpace(o.StringValue())
			if v != "" {
				return v
			}
		}
	}
	return fallback
}
func moderatorID(i *discordgo.InteractionCreate) string {
	if i.Member != nil && i.Member.User != nil {
		return i.Member.User.ID
	}
	return ""
}

func ephemeralEmbed(s *discordgo.Session, i *discordgo.InteractionCreate, embed *discordgo.MessageEmbed) {
	_ = s.InteractionRespond(i.Interaction, &discordgo.InteractionResponse{Type: discordgo.InteractionResponseChannelMessageWithSource, Data: &discordgo.InteractionResponseData{Embeds: []*discordgo.MessageEmbed{embed}, Flags: discordgo.MessageFlagsEphemeral}})
}

func successEmbed(description string) *discordgo.MessageEmbed {
	return &discordgo.MessageEmbed{Color: 0x57F287, Description: description}
}

func errorEmbed(description string) *discordgo.MessageEmbed {
	return &discordgo.MessageEmbed{Color: 0xED4245, Description: description}
}

func infoEmbed(title, description string) *discordgo.MessageEmbed {
	return &discordgo.MessageEmbed{Color: 0x5865F2, Title: title, Description: description}
}

func (b *Bot) kick(s *discordgo.Session, i *discordgo.InteractionCreate) {
	u := optionUser(s, i.ApplicationCommandData(), i.GuildID)
	if u == nil {
		ephemeralEmbed(s, i, errorEmbed("User not found."))
		return
	}
	reason := optionString(i.ApplicationCommandData(), "reason", "No reason provided")
	if err := s.GuildMemberDeleteWithReason(i.GuildID, u.ID, reason); err != nil {
		ephemeralEmbed(s, i, errorEmbed("Kick failed: "+err.Error()))
		return
	}
	b.modLog(i.GuildID, "kick", moderatorID(i), u.ID, i.ChannelID, reason)
	ephemeralEmbed(s, i, successEmbed(fmt.Sprintf("Kicked %s.\n**Reason:** %s", u.Mention(), reason)))
}

func (b *Bot) ban(s *discordgo.Session, i *discordgo.InteractionCreate) {
	u := optionUser(s, i.ApplicationCommandData(), i.GuildID)
	if u == nil {
		ephemeralEmbed(s, i, errorEmbed("User not found."))
		return
	}
	reason := optionString(i.ApplicationCommandData(), "reason", "No reason provided")
	if err := s.GuildBanCreateWithReason(i.GuildID, u.ID, reason, 0); err != nil {
		ephemeralEmbed(s, i, errorEmbed("Ban failed: "+err.Error()))
		return
	}
	b.modLog(i.GuildID, "ban", moderatorID(i), u.ID, i.ChannelID, reason)
	ephemeralEmbed(s, i, successEmbed(fmt.Sprintf("Banned %s.\n**Reason:** %s", u.Mention(), reason)))
}

func (b *Bot) warn(s *discordgo.Session, i *discordgo.InteractionCreate) {
	u := optionUser(s, i.ApplicationCommandData(), i.GuildID)
	if u == nil {
		ephemeralEmbed(s, i, errorEmbed("User not found."))
		return
	}
	reason := optionString(i.ApplicationCommandData(), "reason", "")
	id, err := b.store.AddWarning(context.Background(), i.GuildID, u.ID, moderatorID(i), reason)
	if err != nil {
		ephemeralEmbed(s, i, errorEmbed("Could not save warning."))
		return
	}
	b.modLog(i.GuildID, "warning", moderatorID(i), u.ID, i.ChannelID, fmt.Sprintf("#%d: %s", id, reason))
	settings, _ := b.store.Settings(context.Background(), i.GuildID)
	if settings.DMWarnings {
		ch, err := s.UserChannelCreate(u.ID)
		if err == nil {
			_, _ = s.ChannelMessageSendEmbed(ch.ID, &discordgo.MessageEmbed{
				Color:       0xFEE75C,
				Title:       "You received a warning",
				Description: fmt.Sprintf("**Server:** %s\n**Reason:** %s", guildName(s, i.GuildID), reason),
			})
		}
	}
	ephemeralEmbed(s, i, successEmbed(fmt.Sprintf("Warning #%d issued to %s.", id, u.Mention())))
}

func (b *Bot) warnings(s *discordgo.Session, i *discordgo.InteractionCreate) {
	u := optionUser(s, i.ApplicationCommandData(), i.GuildID)
	if u == nil {
		ephemeralEmbed(s, i, errorEmbed("User not found."))
		return
	}
	ws, err := b.store.Warnings(context.Background(), i.GuildID, u.ID)
	if err != nil {
		ephemeralEmbed(s, i, errorEmbed("Could not load warnings."))
		return
	}
	if len(ws) == 0 {
		ephemeralEmbed(s, i, infoEmbed("Warnings", fmt.Sprintf("No warnings found for %s.", u.Mention())))
		return
	}
	var lines []string
	for idx, w := range ws {
		if idx >= 10 {
			break
		}
		lines = append(lines, fmt.Sprintf("`#%d` %s — <@%s> — %s", w.ID, w.CreatedAt.Format("2006-01-02"), w.ModeratorID, w.Reason))
	}
	ephemeralEmbed(s, i, infoEmbed(fmt.Sprintf("Warnings for %s (%d total)", u.String(), len(ws)), strings.Join(lines, "\n")))
}

func (b *Bot) userinfo(s *discordgo.Session, i *discordgo.InteractionCreate) {
	u := optionUser(s, i.ApplicationCommandData(), i.GuildID)
	if u == nil {
		ephemeralEmbed(s, i, errorEmbed("User not found."))
		return
	}
	m, err := s.GuildMember(i.GuildID, u.ID)
	if err != nil {
		ephemeralEmbed(s, i, errorEmbed("Could not load member information."))
		return
	}
	joined := "Unknown"
	if !m.JoinedAt.IsZero() {
		joined = m.JoinedAt.Format(time.RFC1123)
	}
	created := discordTime(u.ID).Format(time.RFC1123)
	roles := "None"
	if len(m.Roles) > 0 {
		var x []string
		for _, r := range m.Roles {
			x = append(x, "<@&"+r+">")
		}
		roles = strings.Join(x, " ")
	}
	ephemeralEmbed(s, i, &discordgo.MessageEmbed{
		Color:     0x5865F2,
		Title:     u.String(),
		Thumbnail: &discordgo.MessageEmbedThumbnail{URL: u.AvatarURL("128")},
		Fields: []*discordgo.MessageEmbedField{
			{Name: "ID", Value: u.ID, Inline: true},
			{Name: "Created", Value: created, Inline: true},
			{Name: "Joined", Value: joined, Inline: true},
			{Name: "Roles", Value: roles},
		},
	})
}

func discordTime(id string) time.Time {
	var snowflake int64
	_, _ = fmt.Sscan(id, &snowflake)
	ms := (snowflake >> 22) + 1420070400000
	return time.UnixMilli(ms)
}

func (b *Bot) lock(s *discordgo.Session, i *discordgo.InteractionCreate) {
	reason := optionString(i.ApplicationCommandData(), "reason", "Channel lockdown")
	ch, err := s.Channel(i.ChannelID)
	if err != nil {
		ephemeralEmbed(s, i, errorEmbed("Lock failed: could not load channel permissions."))
		return
	}
	var had bool
	var allow, deny int64
	for _, ow := range ch.PermissionOverwrites {
		if ow.ID == i.GuildID && ow.Type == discordgo.PermissionOverwriteTypeRole {
			had = true
			allow = ow.Allow
			deny = ow.Deny
			break
		}
	}
	if err := b.store.SaveChannelLock(context.Background(), i.GuildID, i.ChannelID, had, allow, deny); err != nil {
		ephemeralEmbed(s, i, errorEmbed("Lock failed: could not snapshot permissions."))
		return
	}
	newAllow := allow &^ discordgo.PermissionSendMessages
	newDeny := deny | discordgo.PermissionSendMessages
	if err := s.ChannelPermissionSet(i.ChannelID, i.GuildID, discordgo.PermissionOverwriteTypeRole, newAllow, newDeny); err != nil {
		ephemeralEmbed(s, i, errorEmbed("Lock failed: "+err.Error()))
		return
	}
	b.modLog(i.GuildID, "channel_lock", moderatorID(i), "", i.ChannelID, reason)
	ephemeralEmbed(s, i, successEmbed("Channel locked. Previous permissions were snapshotted for restoration."))
}
func (b *Bot) unlock(s *discordgo.Session, i *discordgo.InteractionCreate) {
	had, allow, deny, found, err := b.store.ChannelLock(context.Background(), i.GuildID, i.ChannelID)
	if err != nil {
		ephemeralEmbed(s, i, errorEmbed("Unlock failed: could not read permission snapshot."))
		return
	}
	if !found {
		ephemeralEmbed(s, i, errorEmbed("No lockdown snapshot exists for this channel."))
		return
	}
	if had {
		err = s.ChannelPermissionSet(i.ChannelID, i.GuildID, discordgo.PermissionOverwriteTypeRole, allow, deny)
	} else {
		err = s.ChannelPermissionDelete(i.ChannelID, i.GuildID)
	}
	if err != nil {
		ephemeralEmbed(s, i, errorEmbed("Unlock failed: "+err.Error()))
		return
	}
	_ = b.store.DeleteChannelLock(context.Background(), i.GuildID, i.ChannelID)
	b.modLog(i.GuildID, "channel_unlock", moderatorID(i), "", i.ChannelID, "Channel permissions restored")
	ephemeralEmbed(s, i, successEmbed("Channel unlocked and previous permissions restored."))
}

func guildName(s *discordgo.Session, id string) string {
	g, err := s.State.Guild(id)
	if err == nil {
		return g.Name
	}
	return "this server"
}

func (b *Bot) modLog(guildID, event, actor, target, channel, details string) {
	_ = b.store.Audit(context.Background(), guildID, event, actor, target, channel, details)
	settings, err := b.store.Settings(context.Background(), guildID)
	if err != nil || !settings.LogModeration || settings.LogChannelID == "" {
		return
	}
	_, _ = b.session.ChannelMessageSendEmbed(settings.LogChannelID, &discordgo.MessageEmbed{Title: "Moderation event: " + event, Description: details, Fields: []*discordgo.MessageEmbedField{{Name: "Moderator", Value: "<@" + actor + ">", Inline: true}, {Name: "Target", Value: mentionOrDash(target), Inline: true}, {Name: "Channel", Value: channelMention(channel), Inline: true}}, Timestamp: time.Now().Format(time.RFC3339)})
}
func mentionOrDash(id string) string {
	if id == "" {
		return "—"
	}
	return "<@" + id + ">"
}
func channelMention(id string) string {
	if id == "" {
		return "—"
	}
	return "<#" + id + ">"
}

func (b *Bot) onMessageDelete(s *discordgo.Session, m *discordgo.MessageDelete) {
	if m.GuildID == "" {
		return
	}
	settings, err := b.store.Settings(context.Background(), m.GuildID)
	if err != nil || !settings.LogDeletes {
		return
	}
	content := "Content unavailable (not cached)."
	author := "unknown"
	if m.BeforeDelete != nil {
		content = m.BeforeDelete.Content
		if m.BeforeDelete.Author != nil {
			author = m.BeforeDelete.Author.ID
		}
	}
	_ = b.store.Audit(context.Background(), m.GuildID, "message_delete", author, "", m.ChannelID, truncate(content, 1800))
	if settings.LogChannelID != "" {
		_, _ = s.ChannelMessageSendEmbed(settings.LogChannelID, &discordgo.MessageEmbed{Title: "Message deleted", Description: truncate(content, 3500), Fields: []*discordgo.MessageEmbedField{{Name: "Author", Value: mentionOrDash(author), Inline: true}, {Name: "Channel", Value: channelMention(m.ChannelID), Inline: true}}, Timestamp: time.Now().Format(time.RFC3339)})
	}
}

func (b *Bot) onMessageUpdate(s *discordgo.Session, m *discordgo.MessageUpdate) {
	if m.GuildID == "" || m.BeforeUpdate == nil || m.Message == nil || m.Author == nil || m.Author.Bot {
		return
	}
	if m.BeforeUpdate.Content == m.Content {
		return
	}
	settings, err := b.store.Settings(context.Background(), m.GuildID)
	if err != nil || !settings.LogEdits {
		return
	}
	details := fmt.Sprintf("Before: %s\nAfter: %s", truncate(m.BeforeUpdate.Content, 700), truncate(m.Content, 700))
	_ = b.store.Audit(context.Background(), m.GuildID, "message_edit", m.Author.ID, "", m.ChannelID, details)
	if settings.LogChannelID != "" {
		_, _ = s.ChannelMessageSendEmbed(settings.LogChannelID, &discordgo.MessageEmbed{Title: "Message edited", Fields: []*discordgo.MessageEmbedField{{Name: "Author", Value: m.Author.Mention(), Inline: true}, {Name: "Channel", Value: channelMention(m.ChannelID), Inline: true}, {Name: "Before", Value: truncate(m.BeforeUpdate.Content, 1000)}, {Name: "After", Value: truncate(m.Content, 1000)}}, Timestamp: time.Now().Format(time.RFC3339)})
	}
}
func truncate(v string, n int) string {
	v = strings.TrimSpace(v)
	if v == "" {
		return "(empty)"
	}
	if len(v) <= n {
		return v
	}
	return v[:n-1] + "…"
}
