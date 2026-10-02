// duckgrep.dev: copy buttons, the question tabs and the race replay.
// The page is complete without this file: every step and total is in the HTML, and this only animates it.
(() => {
  "use strict";

  const reduced = matchMedia("(prefers-reduced-motion: reduce)");

  // --- copy ---------------------------------------------------------------
  for (const btn of document.querySelectorAll("[data-copy], [data-copy-from]")) {
    btn.addEventListener("click", async () => {
      const text = btn.dataset.copy ?? document.getElementById(btn.dataset.copyFrom).textContent.trim();
      const label = btn.textContent;
      try {
        await navigator.clipboard.writeText(text);
        btn.textContent = "Copied";
      } catch {
        const code = btn.parentElement.querySelector(".cmd__text");
        getSelection().selectAllChildren(code);
        btn.textContent = "Selected";
      }
      setTimeout(() => (btn.textContent = label), 1600);
    });
  }

  // --- wide output ----------------------------------------------------------
  // terminal output stays verbatim, so a line wider than its lane scrolls; say so where it does
  const wide = () => {
    for (const out of document.querySelectorAll(".step__out, .how__term .dg-term")) {
      const isWide = out.scrollWidth > out.clientWidth + 1;
      const cue = out.nextElementSibling?.classList.contains("scroll-cue") ? out.nextElementSibling : null;
      if (isWide && !cue) out.insertAdjacentHTML("afterend", '<p class="scroll-cue" aria-hidden="true">scroll sideways for the rest →</p>');
      if (!isWide && cue) cue.remove();
    }
  };
  addEventListener("resize", wide);
  addEventListener("load", wide);
  document.querySelectorAll('[role="tab"]').forEach((t) => t.addEventListener("click", () => requestAnimationFrame(wide)));

  // --- the race -----------------------------------------------------------
  const race = document.querySelector(".race");
  if (!race) return;
  const panels = [...race.querySelectorAll(".race__q")];
  const tabs = [...race.querySelectorAll('[role="tab"]')];
  const replay = race.querySelector(".race__replay");
  const ease = (t) => 1 - Math.pow(2, -10 * t);

  const fmt = {
    calls: (n) => String(Math.round(n)),
    tokens: (n) => (n / 1000).toFixed(1) + "k",
    cost: (n) => "$" + n.toFixed(4),
  };

  let timers = [];
  let frames = new Map();
  const later = (fn, ms) => timers.push(setTimeout(fn, ms));

  function tween(dd, key, from, to) {
    cancelAnimationFrame(frames.get(dd));
    const t0 = performance.now();
    const step = (now) => {
      const t = Math.min(1, (now - t0) / 500);
      dd.textContent = fmt[key](from + (to - from) * ease(t));
      if (t < 1) frames.set(dd, requestAnimationFrame(step));
    };
    frames.set(dd, requestAnimationFrame(step));
  }

  function status(lane, text) {
    let s = lane.querySelector(".lane__status");
    if (!s) {
      s = document.createElement("span");
      s.className = "lane__status";
      lane.querySelector(".lane__bar").append(s);
    }
    s.textContent = text;
  }

  function stop() {
    timers.forEach(clearTimeout);
    timers = [];
    frames.forEach((id) => cancelAnimationFrame(id));
    frames = new Map();
  }

  // show a panel's final state: every step, the recorded totals
  function settle(panel) {
    for (const lane of panel.querySelectorAll(".lane")) {
      const steps = lane.querySelectorAll(".step");
      steps.forEach((s) => s.classList.remove("is-pending"));
      const last = steps[steps.length - 1];
      for (const dd of lane.querySelectorAll("[data-meter]")) {
        const key = dd.dataset.meter;
        dd.textContent = fmt[key](Number(last.dataset[key]));
      }
      lane.classList.remove("is-running");
      lane.classList.add("is-done");
      status(lane, "done in " + (Number(last.dataset.ms) / 1000).toFixed(1) + " s");
    }
    panel.querySelector("[data-result]").classList.remove("is-pending");
    panel.querySelectorAll(".meter__d").forEach((d) => d.classList.remove("is-pending"));
  }

  // hide what hasn't happened yet and zero the counters
  function reset(panel) {
    for (const lane of panel.querySelectorAll(".lane")) {
      lane.querySelectorAll(".step").forEach((s) => s.classList.add("is-pending"));
      lane.querySelectorAll("[data-meter]").forEach((dd) => (dd.textContent = fmt[dd.dataset.meter](0)));
      lane.classList.remove("is-done", "is-running");
      status(lane, "ready");
    }
    panel.querySelector("[data-result]").classList.add("is-pending");
    panel.querySelectorAll(".meter__d").forEach((d) => d.classList.add("is-pending"));
  }

  function play(panel) {
    stop();
    if (reduced.matches) return settle(panel);
    reset(panel);
    void panel.offsetWidth; // let the reset paint before the first step lands
    const lanes = [...panel.querySelectorAll(".lane")];
    let running = lanes.length;
    for (const lane of lanes) {
      lane.classList.add("is-running");
      status(lane, "working");
      const shown = { calls: 0, tokens: 0, cost: 0 };
      const steps = [...lane.querySelectorAll(".step")];
      steps.forEach((step, i) => {
        later(() => {
          step.classList.remove("is-pending");
          for (const dd of lane.querySelectorAll("[data-meter]")) {
            const key = dd.dataset.meter;
            const to = Number(step.dataset[key]);
            if (to !== shown[key]) tween(dd, key, shown[key], to);
            shown[key] = to;
          }
          if (i === steps.length - 1) {
            lane.classList.replace("is-running", "is-done");
            status(lane, "done in " + (Number(step.dataset.ms) / 1000).toFixed(1) + " s");
            if (--running === 0) {
              // the differences only mean something once both runs have finished
              later(() => {
                panel.querySelectorAll(".meter__d").forEach((d) => d.classList.remove("is-pending"));
                panel.querySelector("[data-result]").classList.remove("is-pending");
              }, 400);
            }
          }
        }, Number(step.dataset.ms));
      });
    }
  }

  // tabs: one question at a time once the script runs; without it, both are on the page
  const tablist = race.querySelector('[role="tablist"]');
  tablist.hidden = false;
  replay.hidden = false;
  let current = panels[0];

  function select(tab, focus) {
    for (const t of tabs) {
      const on = t === tab;
      t.setAttribute("aria-selected", String(on));
      t.tabIndex = on ? 0 : -1;
      document.getElementById(t.getAttribute("aria-controls")).hidden = !on;
    }
    if (focus) tab.focus();
    current = document.getElementById(tab.getAttribute("aria-controls"));
    play(current);
  }

  tabs.forEach((tab, i) => {
    tab.addEventListener("click", () => select(tab, false));
    tab.addEventListener("keydown", (e) => {
      const d = { ArrowRight: 1, ArrowLeft: -1 }[e.key];
      if (d) {
        e.preventDefault();
        select(tabs[(i + d + tabs.length) % tabs.length], true);
      }
    });
  });
  panels.slice(1).forEach((p) => (p.hidden = true));
  replay.addEventListener("click", () => play(current));

  // play once, the first time the race is in view
  if (reduced.matches) {
    settle(current);
  } else {
    reset(current);
    const seen = new IntersectionObserver(
      (entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          seen.disconnect();
          play(current);
        }
      },
      { rootMargin: "0px 0px -15% 0px" }, // the lanes can be taller than the screen, so watch their top
    );
    seen.observe(current.querySelector(".race__lanes"));
  }
  reduced.addEventListener("change", () => {
    stop();
    settle(current);
  });
})();
