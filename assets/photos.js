// Weekly photo strip: shows every image in images/weekly/ of this repo.
// Photos are uploaded by hand on GitHub (photos.html links to the upload
// page) and the Clear Weekly Photos workflow empties the folder Tuesday.
(function () {
  const API = "https://api.github.com/repos/WiggyWigs/TheHook/contents/";
  const IMAGE_EXTENSIONS = /\.(jpe?g|png|gif|webp)$/i;
  const esc = (s) =>
    String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // Image files in the folder; [] if it's missing, empty or GitHub can't be reached
  async function listPhotos(folder) {
    try {
      const res = await fetch(API + folder, { cache: "no-store" });
      if (!res.ok) return [];
      const files = await res.json();
      return Array.isArray(files) ? files.filter((f) => f.type === "file" && IMAGE_EXTENSIONS.test(f.name)) : [];
    } catch (e) {
      return [];
    }
  }

  // Fill the element with linked thumbnails; hide it when there are none
  async function showPhotos(folder, elId) {
    const el = document.getElementById(elId);
    const files = await listPhotos(folder);
    el.innerHTML = files.map((f) => `
      <a href="${esc(f.download_url)}" target="_blank" rel="noopener"><img src="${esc(f.download_url)}" alt="${esc(f.name)}"></a>`).join("");
    el.style.display = files.length ? "flex" : "none";
    // Open the photo in a popup instead of a new tab (the link still works without JS)
    el.querySelectorAll("a").forEach((a, i) =>
      a.addEventListener("click", (e) => {
        e.preventDefault();
        openLightbox(files.map((f) => f.download_url), i);
      }));
    return files;
  }

  // Full-screen popup: tap outside / × / Esc closes, arrows or swipe step through
  let box, boxImg, urls = [], index = 0;
  function buildLightbox() {
    box = document.createElement("div");
    box.className = "lightbox";
    box.innerHTML = `
      <button class="lb-close" aria-label="Close">&times;</button>
      <button class="lb-prev" aria-label="Previous">&lsaquo;</button>
      <img alt="">
      <button class="lb-next" aria-label="Next">&rsaquo;</button>`;
    boxImg = box.querySelector("img");
    box.addEventListener("click", (e) => { if (e.target === box) closeLightbox(); });
    box.querySelector(".lb-close").addEventListener("click", closeLightbox);
    box.querySelector(".lb-prev").addEventListener("click", () => step(-1));
    box.querySelector(".lb-next").addEventListener("click", () => step(1));
    document.addEventListener("keydown", (e) => {
      if (!box.classList.contains("open")) return;
      if (e.key === "Escape") closeLightbox();
      else if (e.key === "ArrowLeft") step(-1);
      else if (e.key === "ArrowRight") step(1);
    });
    let startX = null;
    box.addEventListener("touchstart", (e) => { startX = e.touches[0].clientX; }, { passive: true });
    box.addEventListener("touchend", (e) => {
      if (startX === null) return;
      const dx = e.changedTouches[0].clientX - startX;
      startX = null;
      if (Math.abs(dx) > 50) step(dx < 0 ? 1 : -1);
    });
    document.body.appendChild(box);
  }

  function openLightbox(list, i) {
    if (!box) buildLightbox();
    urls = list;
    const multi = urls.length > 1;
    box.querySelector(".lb-prev").style.display = multi ? "" : "none";
    box.querySelector(".lb-next").style.display = multi ? "" : "none";
    show(i);
    box.classList.add("open");
    document.body.style.overflow = "hidden";
  }

  function show(i) {
    index = (i + urls.length) % urls.length;
    boxImg.src = urls[index];
  }

  function step(d) { show(index + d); }

  function closeLightbox() {
    box.classList.remove("open");
    document.body.style.overflow = "";
  }

  window.HookPhotos = { listPhotos, showPhotos };
})();
