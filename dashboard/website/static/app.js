document
  .querySelector("[data-menu]")
  ?.addEventListener("click", () =>
    document.getElementById("sidebar")?.classList.toggle("open"),
  );
document.addEventListener("click", (event) => {
  const sidebar = document.getElementById("sidebar");
  const button = document.querySelector("[data-menu]");
  if (
    window.innerWidth <= 900 &&
    sidebar?.classList.contains("open") &&
    !sidebar.contains(event.target) &&
    !button?.contains(event.target)
  )
    sidebar.classList.remove("open");
});
