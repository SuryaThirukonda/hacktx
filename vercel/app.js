// Situation picker and replay player for the static Vercel site. A situation is opened with ?s=<scenario id>.
const $ = (id) => document.getElementById(id);

function card(s, i) {
  const a = document.createElement("a");
  a.className = "card";
  a.href = `/?s=${encodeURIComponent(s.sid)}`;
  a.style.backgroundImage = `url("/${s.image}")`;
  a.innerHTML = `<span class="num">0${i + 1}</span><span class="tag">Simulated · ${s.mission}</span>
    <h2></h2><p></p><span class="go">▶ Play recorded run</span>`;
  a.querySelector("h2").textContent = s.name;
  a.querySelector("p").textContent = s.summary;
  return a;
}

function show(s) {
  document.title = `Overwatch · ${s.name} (recorded)`;
  $("home").hidden = true;
  $("player").hidden = false;
  $("p-name").textContent = s.name;
  $("p-sid").textContent = s.sid;
  $("p-title").textContent = s.title;
  $("p-detail").textContent = s.detail;
  const v = $("p-video");
  v.src = `/${s.video}`;
  v.play().catch(() => {}); // autoplay can be refused; the controls stay available
}

fetch("/situations.json")
  .then((r) => r.json())
  .then((list) => {
    const wanted = new URLSearchParams(location.search).get("s");
    const hit = wanted && list.find((s) => s.sid === wanted);
    if (hit) return show(hit);
    list.forEach((s, i) => $("cards").append(card(s, i)));
  });
