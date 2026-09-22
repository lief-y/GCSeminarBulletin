const DATA_URL = "data/seminars.json";

const DAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"];

let allSeminars = [];

document.addEventListener("DOMContentLoaded", init);

async function loadData() {
  // 1. Inlined data (used for local file:// preview)
  if (window.__SEMINARS_DATA__) {
    return window.__SEMINARS_DATA__;
  }
  // 2. Fall back to fetch (used on GitHub Pages and local servers)
  const res = await fetch(DATA_URL, { cache: "no-store" });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return await res.json();
}

async function init() {
  try {
    const data = await loadData();
    allSeminars = (data.seminars || []).slice();

    const updatedEl = document.getElementById("last-updated");
    if (data.last_updated) {
      const dt = new Date(data.last_updated);
      updatedEl.textContent = `Last updated: ${dt.toLocaleString()}`;
    }

    render(allSeminars);

    document.getElementById("searchBox").addEventListener("input", onSearch);
    document.getElementById("hideEmpty").addEventListener("change", () =>
      render(filterBySearch(allSeminars))
    );
  } catch (err) {
    console.error("Failed to load seminars:", err);
    document.getElementById("timetable").innerHTML =
      '<p class="no-results">Could not load seminar data. ' +
      'If you opened this file directly, try rebuilding the preview with <code>python dev.py --scrape</code>.</p>';
  }
}

function onSearch() {
  render(filterBySearch(allSeminars));
}

function filterBySearch(list) {
  const q = document.getElementById("searchBox").value.trim().toLowerCase();
  if (!q) return list;
  return list.filter((s) => {
    const hay = [s.speaker, s.title, s.seminar, s.name, s.abstract]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    return hay.includes(q);
  });
}

/* ---------- date helpers ---------- */

function startOfWeek(d) {
  const date = new Date(d);
  const day = date.getDay();
  const diff = day === 0 ? -6 : 1 - day;
  date.setDate(date.getDate() + diff);
  date.setHours(0, 0, 0, 0);
  return date;
}

function addDays(d, n) {
  const x = new Date(d);
  x.setDate(x.getDate() + n);
  return x;
}

function formatDateISO(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function formatWeekLabel(monday) {
  const friday = addDays(monday, 4);
  const opts = { month: "short", day: "numeric" };
  const yearOpts = { year: "numeric" };
  return `Week of ${monday.toLocaleDateString(undefined, opts)} – ${friday.toLocaleDateString(
    undefined,
    opts
  )}, ${monday.toLocaleDateString(undefined, yearOpts)}`;
}

function timeToMinutes(t) {
  if (!t) return 0;
  const m = t.match(/(\d{1,2}):(\d{2})\s*(AM|PM|am|pm)/);
  if (!m) return 0;
  let h = parseInt(m[1], 10);
  const min = parseInt(m[2], 10);
  const ap = m[3].toUpperCase();
  if (ap === "PM" && h !== 12) h += 12;
  if (ap === "AM" && h === 12) h = 0;
  return h * 60 + min;
}

/* ---------- render ---------- */

function render(seminars) {
  const timetable = document.getElementById("timetable");
  const noResults = document.getElementById("noResults");
  timetable.innerHTML = "";

  if (!seminars.length) {
    noResults.hidden = false;
    return;
  }
  noResults.hidden = true;

  if (seminars.some((s) => s.date)) {
    renderByDates(seminars);
  } else {
    renderByDay(seminars);
  }
}

function renderByDates(seminars) {
  const hideEmpty = document.getElementById("hideEmpty").checked;
  const timetable = document.getElementById("timetable");

  const today = new Date();
  const thisMonday = startOfWeek(today);
  const nextMonday = addDays(thisMonday, 7);

  const weeks = [
    { monday: thisMonday, label: formatWeekLabel(thisMonday) },
    { monday: nextMonday, label: formatWeekLabel(nextMonday) },
  ];

  for (const week of weeks) {
    const dayISO = {};
    for (let i = 0; i < 5; i++) {
      dayISO[DAY_ORDER[i]] = formatDateISO(addDays(week.monday, i));
    }

    const byDay = {};
    for (const day of DAY_ORDER) byDay[day] = [];
    for (const s of seminars) {
      if (!s.date) continue;
      for (const day of DAY_ORDER) {
        if (s.date === dayISO[day]) {
          byDay[day].push(s);
          break;
        }
      }
    }

    for (const day of DAY_ORDER) {
      byDay[day].sort((a, b) => timeToMinutes(a.time) - timeToMinutes(b.time));
    }

    const total = DAY_ORDER.reduce((n, d) => n + byDay[d].length, 0);
    if (total === 0 && hideEmpty) continue;

    const weekEl = document.createElement("section");
    weekEl.className = "week";

    const title = document.createElement("h2");
    title.className = "week-title";
    title.textContent = week.label;
    weekEl.appendChild(title);

    weekEl.appendChild(buildGrid(byDay, DAY_ORDER));
    timetable.appendChild(weekEl);
  }
}

function renderByDay(seminars) {
  const hideEmpty = document.getElementById("hideEmpty").checked;
  const timetable = document.getElementById("timetable");

  const byDay = {};
  for (const day of DAY_ORDER) byDay[day] = [];
  for (const s of seminars) {
    const d = s.day;
    if (byDay[d]) byDay[d].push(s);
  }

  for (const day of DAY_ORDER) {
    byDay[day].sort((a, b) => timeToMinutes(a.time) - timeToMinutes(b.time));
  }

  const total = DAY_ORDER.reduce((n, d) => n + byDay[d].length, 0);
  if (total === 0 && hideEmpty) return;

  const weekEl = document.createElement("section");
  weekEl.className = "week";

  const title = document.createElement("h2");
  title.className = "week-title";
  title.textContent = "This Week's Seminars";
  weekEl.appendChild(title);

  weekEl.appendChild(buildGrid(byDay, DAY_ORDER));
  timetable.appendChild(weekEl);
}

function buildGrid(byDay, days) {
  const grid = document.createElement("div");
  grid.className = "week-grid";

  for (const day of days) {
    const col = document.createElement("div");
    col.className = "day-col";

    const header = document.createElement("div");
    header.className = "day-header";
    header.textContent = day;
    col.appendChild(header);

    const body = document.createElement("div");
    body.className = "day-body";

    if (!byDay[day] || byDay[day].length === 0) {
      const empty = document.createElement("div");
      empty.className = "day-empty";
      empty.textContent = "—";
      body.appendChild(empty);
    } else {
      for (const s of byDay[day]) body.appendChild(renderCard(s));
    }

    col.appendChild(body);
    grid.appendChild(col);
  }
  return grid;
}

function renderCard(s) {
  const card = document.createElement("div");
  card.className = "card";

  const timeLoc = [s.time, s.location || s.room].filter(Boolean).join(" · ");
  if (timeLoc) {
    const t = document.createElement("div");
    t.className = "card-time";
    t.textContent = timeLoc;
    card.appendChild(t);
  }

  const seminarName = s.seminar || s.name;
  if (seminarName) {
    const sem = document.createElement("div");
    sem.className = "card-seminar";
    sem.textContent = seminarName;
    card.appendChild(sem);
  }

  if (s.speaker) {
    const sp = document.createElement("div");
    sp.className = "card-speaker";
    sp.textContent = s.speaker;
    card.appendChild(sp);
  }

  if (s.title) {
    const ti = document.createElement("div");
    ti.className = "card-title";
    ti.textContent = s.title;
    card.appendChild(ti);
  }

  const abstract = document.createElement("div");
  abstract.className = "card-abstract";
  if (s.abstract) {
    const p = document.createElement("p");
    p.textContent = s.abstract;
    p.style.margin = "0";
    abstract.appendChild(p);
  }
  if (s.website) {
    const a = document.createElement("a");
    a.href = s.website;
    a.target = "_blank";
    a.rel = "noopener";
    a.textContent = "Seminar website →";
    abstract.appendChild(a);
  }
  if (abstract.childNodes.length) {
    card.appendChild(abstract);
    card.addEventListener("click", (e) => {
      if (e.target.tagName === "A") return;
      card.classList.toggle("expanded");
    });
  }

  return card;
}
