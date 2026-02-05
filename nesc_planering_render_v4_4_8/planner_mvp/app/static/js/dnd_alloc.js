(function () {
  function setDragData(e, value) {
    try { e.dataTransfer.setData("text/plain", value); } catch (_) {}
    e.dataTransfer.effectAllowed = "move";
  }

  
  function fmtHours(mins){
    const h = (mins||0)/60;
    // show 0.5/1 decimal; keep one decimal but strip .0
    let s = (Math.round(h*10)/10).toFixed(1);
    if(s.endsWith(".0")) s = s.slice(0,-2);
    return s + "h";
  }

function bind() {
    const projects = (window.__projects || []).slice();

    // ---------- Undo stack (persist across reloads) ----------
    const UNDO_KEY = "planner_undo_stack_v4";
    function loadUndo() {
      try { return JSON.parse(sessionStorage.getItem(UNDO_KEY) || "[]") || []; } catch (_) { return []; }
    }
    function saveUndo(stack) {
      try { sessionStorage.setItem(UNDO_KEY, JSON.stringify(stack.slice(-50))); } catch (_) {}
    }
    let undoStack = loadUndo();

    function pushUndo(action) {
      undoStack.push(action);
      if (undoStack.length > 50) undoStack = undoStack.slice(-50);
      saveUndo(undoStack);
    }

    async function doUndo() {
      const action = undoStack.pop();
      saveUndo(undoStack);
      if (!action) return;

      try {
        if (action.t === "create") {
          await fetch(`/api/allocations/${action.id}`, { method: "DELETE" });
          window.location.reload();
          return;
        }
        if (action.t === "update") {
          await fetch(`/api/allocations/${action.id}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(Object.assign({}, action.prev, {allow_over: true}))
          });
          window.location.reload();
          return;
        }
        if (action.t === "delete") {
          // recreate (new id) – good enough for undo
          await fetch("/api/allocations", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(Object.assign({}, action.data, {allow_over: true}))
          });
          window.location.reload();
          return;
        }
      } catch (e) {
        alert("Undo misslyckades: " + e);
      }
    }

    document.addEventListener("keydown", (e) => {
      const key = (e.key || "").toLowerCase();
      if ((e.ctrlKey || e.metaKey) && key === "z") {
        e.preventDefault();
        doUndo();
      }
      if (key === "escape") hideCtx();
    });

    // ---------- Context menu ----------
    let ctx = document.getElementById("ctxMenu");
    if (!ctx) {
      ctx = document.createElement("div");
      ctx.id = "ctxMenu";
      ctx.className = "ctx-menu";
      document.body.appendChild(ctx);
    }

    let selecting = null;   // active pointer drag selection (during mouse move)
    let activeRange = null; // committed selection {personId, lo, hi, start, end}

    function clearSelection() {
      document.querySelectorAll(".dropcell.sel").forEach((el) => el.classList.remove("sel"));
      selecting = null;
      activeRange = null;
    }

    function hideCtx() {
      ctx.style.display = "none";
      ctx.innerHTML = "";
      clearSelection();
    }

    let ctxJustOpenedAt = 0;
    function showCtx(x, y, htmlBuilder) {
      ctx.innerHTML = "";
      htmlBuilder(ctx);
      const w = 280;
      const h = 380;
      ctx.style.left = Math.max(10, Math.min(x, window.innerWidth - w)) + "px";
      ctx.style.top = Math.max(10, Math.min(y, window.innerHeight - h)) + "px";
      ctx.style.display = "block";
      ctxJustOpenedAt = Date.now();
    }

    document.addEventListener("click", (e) => {
      if (Date.now() - ctxJustOpenedAt < 220) return;
      if (!ctx.contains(e.target)) hideCtx();
    });

    // ---------- API helpers ----------
    function _fmtHours(mins){
  const h = (mins||0) / 60;
  return h.toFixed(1);
}

async function _confirmScopeExceeded(js){
  if(!js || js.error !== 'scope_exceeded') return false;
  const msg = `Varning: Detta gör att projektet överskrider scope.

Scope: ${_fmtHours(js.scope)} h
Planerat: ${_fmtHours(js.planned)} h
Överskrider med: ${_fmtHours(js.over)} h

Fortsätta ändå?`;
  return confirm(msg);
}

async function createAlloc(a, b, c, d, e){
  // Stöd både createAlloc(payload) och createAlloc(projectId, personId, start, end, percent)
  let payload;
  if (typeof a === "object" && a !== null) {
    payload = a;
  } else {
    payload = {
      project_id: parseInt(a, 10),
      person_id: parseInt(b, 10),
      start_date: c,
      end_date: d,
      percent: parseInt(e, 10)
    };
  }

  // 1) try without override
  let res = await fetch('/api/allocations', {
    method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify(payload)
  });

  if(res.status === 409){
    let js = null;
    try{ js = await res.json(); }catch(e){}
    const ok = await _confirmScopeExceeded(js);
    if(!ok) return null;
    payload = {...payload, allow_over: true};
    res = await fetch('/api/allocations', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(payload)
    });
  }

  if(!res.ok){
    alert('Kunde inte skapa allokering');
    return null;
  }
  const js = await res.json();
  return js.id;
}

async function updateAlloc(id, patch){
  let res = await fetch(`/api/allocations/${id}`, {
    method: 'PUT', headers: {'Content-Type':'application/json'},
    body: JSON.stringify(patch)
  });

  if(res.status === 409){
    let js = null;
    try{ js = await res.json(); }catch(e){}
    const ok = await _confirmScopeExceeded(js);
    if(!ok) return false;
    patch = {...patch, allow_over: true};
    res = await fetch(`/api/allocations/${id}`, {
      method: 'PUT', headers: {'Content-Type':'application/json'},
      body: JSON.stringify(patch)
    });
  }

  if(!res.ok){
    alert('Kunde inte uppdatera allokering');
    return false;
  }
  return true;
}

async function deleteAlloc(allocId) {
      const res = await fetch(`/api/allocations/${allocId}`, { method: "DELETE" });
      if (!res.ok) {
        alert("Kunde inte ta bort (HTTP " + res.status + ")");
        return false;
      }
      return true;
    }

    // ---- Unit allocations (time-based) ----
    async function createUnitAlloc(payload){
      let res = await fetch('/api/unit_allocations', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify(payload)
      });
      if(res.status === 409){
        let js=null; try{js=await res.json()}catch(_){ }
        const ok = await _confirmScopeExceeded(js);
        if(!ok) return null;
        payload = {...payload, allow_over:true};
        res = await fetch('/api/unit_allocations', {
          method:'POST', headers:{'Content-Type':'application/json'},
          body: JSON.stringify(payload)
        });
      }
      if(!res.ok){ alert('Kunde inte skapa enhetsplanering'); return null; }
      const js = await res.json();
      return js.id;
    }

    async function updateUnitAlloc(id, patch){
      let res = await fetch(`/api/unit_allocations/${id}`, {
        method:'PUT', headers:{'Content-Type':'application/json'},
        body: JSON.stringify(patch)
      });
      if(res.status === 409){
        let js=null; try{js=await res.json()}catch(_){ }
        const ok = await _confirmScopeExceeded(js);
        if(!ok) return false;
        patch = {...patch, allow_over:true};
        res = await fetch(`/api/unit_allocations/${id}`, {
          method:'PUT', headers:{'Content-Type':'application/json'},
          body: JSON.stringify(patch)
        });
      }
      if(!res.ok){ alert('Kunde inte uppdatera enhetsplanering'); return false; }
      return true;
    }

    async function deleteUnitAlloc(id){
      const res = await fetch(`/api/unit_allocations/${id}`, {method:'DELETE'});
      if(!res.ok){ alert('Kunde inte ta bort enhetsplanering'); return false; }
      return true;
    }

    // ---- Ad-hoc allocations (% without project) ----
    async function createAdhoc(payload){
      const res = await fetch('/api/adhoc_allocations', {
        method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify(payload)
      });
      if(!res.ok){ alert('Kunde inte skapa Fri text'); return null; }
      const js = await res.json();
      return js.id;
    }

    async function updateAdhoc(id, patch){
      const res = await fetch(`/api/adhoc_allocations/${id}`, {
        method:'PUT', headers:{'Content-Type':'application/json'},
        body: JSON.stringify(patch)
      });
      if(!res.ok){ alert('Kunde inte uppdatera Fri text'); return false; }
      return true;
    }

    async function deleteAdhoc(id){
      const res = await fetch(`/api/adhoc_allocations/${id}`, {method:'DELETE'});
      if(!res.ok){ alert('Kunde inte ta bort Fri text'); return false; }
      return true;
    }

    function datesForRange(personId, lo, hi) {
      const startCell = document.querySelector(`.dropcell[data-person="${personId}"][data-pi="${lo}"]`);
      const endCell = document.querySelector(`.dropcell[data-person="${personId}"][data-pi="${hi}"]`);
      if (!startCell || !endCell) return null;
      return { start: startCell.getAttribute("data-start"), end: endCell.getAttribute("data-end") };
    }

    // NOTE: showCtx uses position:fixed, so coordinates should be viewport/client coords.
    function openAddMenu(clientX, clientY, personId, start, end) {
      showCtx(clientX, clientY, (root) => {
        const title = document.createElement("div");
        title.className = "ctx-title";
        title.textContent = "Lägg till i schemat";
        root.appendChild(title);

        const sub = document.createElement("div");
        sub.className = "ctx-sub";
        sub.textContent = (start === end) ? start : (start + " → " + end);
        root.appendChild(sub);

        // Even if there are no projects, allow ad-hoc (Fri text)

        // Percent picker (default 50)
        let selectedPct = 50;
        const pctTitle = document.createElement("div");
        pctTitle.className = "ctx-title";
        pctTitle.textContent = `Procent: ${selectedPct}%`;
        root.appendChild(pctTitle);

        const pctWrap = document.createElement("div");
        pctWrap.className = "ctx-pcts";
        const pctButtons = [];
        function setPct(v) {
          selectedPct = v;
          pctTitle.textContent = `Procent: ${selectedPct}%`;
          pctButtons.forEach((b) => b.classList.toggle("active", parseInt(b.dataset.pct, 10) === selectedPct));
        }

        [25, 50, 75, 100].forEach((v) => {
          const b = document.createElement("button");
          b.type = "button";
          b.className = "ctx-pct";
          b.dataset.pct = String(v);
          b.textContent = v + "%";
          b.addEventListener("click", (ev) => { ev.stopPropagation(); setPct(v); });
          pctButtons.push(b);
          pctWrap.appendChild(b);
        });

        const bCustom = document.createElement("button");
        bCustom.type = "button";
        bCustom.className = "ctx-pct";
        bCustom.textContent = "Annan…";
        bCustom.addEventListener("click", (ev) => {
          ev.stopPropagation();
          let v = prompt("Procent (0–200):", String(selectedPct));
          if (v === null) return;
          v = parseInt(v, 10);
          if (isNaN(v) || v < 0 || v > 200) { alert("Ogiltig procent."); return; }
          setPct(v);
        });
        pctWrap.appendChild(bCustom);
        root.appendChild(pctWrap);

        const sep = document.createElement("div");
        sep.className = "ctx-sep";
        root.appendChild(sep);

        // Ad-hoc (pink) small tasks
        const adh = document.createElement("div");
        adh.className = "ctx-item";
        adh.innerHTML = `<span class="ctx-dot" style="background:#ff4fa3"></span>Fri text (rosa)…`;
        adh.addEventListener("click", async () => {
          hideCtx();
          const title = (prompt("Rubrik för Fri text:", "Småjobb") || "").trim() || "Småjobb";
          let v = prompt("Procent (0–200):", String(selectedPct));
          if (v === null) return;
          v = parseInt(v, 10);
          if (isNaN(v) || v < 0 || v > 200) { alert("Ogiltig procent."); return; }
          const id = await createAdhoc({
            person_id: parseInt(personId, 10),
            start_date: start,
            end_date: end,
            percent: v,
            title,
            color: "#ff4fa3",
          });
          if (id) window.location.reload();
        });
        root.appendChild(adh);

        const sepP = document.createElement("div");
        sepP.className = "ctx-sep";
        root.appendChild(sepP);

        if (!projects.length) {
          const empty = document.createElement("div");
          empty.className = "ctx-sub";
          empty.textContent = "Inga projekt finns.";
          root.appendChild(empty);
          return;
        }

        projects.forEach((p) => {
          const item = document.createElement("div");
          item.className = "ctx-item";
          item.innerHTML = `<span class="ctx-dot" style="background:${p.color}"></span>${p.name}`;
          item.addEventListener("click", async () => {
            hideCtx();
            const id = await createAlloc(p.id, personId, start, end, selectedPct);
            if (id) {
              pushUndo({ t: "create", id });
              window.location.reload();
            }
          });
          root.appendChild(item);
        });

        const sep2 = document.createElement("div");
        sep2.className = "ctx-sep";
        root.appendChild(sep2);

        const cancel = document.createElement("div");
        cancel.className = "ctx-item";
        cancel.innerHTML = `<span class="ctx-dot" style="background:#94a3b8"></span>Avbryt`;
        cancel.addEventListener("click", () => hideCtx());
        root.appendChild(cancel);

        setPct(50);
      });
    }

    // ---------- Drag & Drop (project chips & bars) ----------
    let isDraggingProject = false;

    document.querySelectorAll(".project-chip").forEach((el) => {
      el.addEventListener("dragstart", (e) => {
        // dragstart bubbles. If the user started dragging a nested workitem, that handler sets the payload.
        // Without this guard, the project handler would overwrite the payload to "project:<id>".
        if (e.target && e.target.closest && e.target.closest(".workitem-chip")) {
          return;
        }
        isDraggingProject = true;
        setDragData(e, "project:" + el.getAttribute("data-project-id"));
        el.classList.add("dragging");
      });
      el.addEventListener("dragend", () => {
        isDraggingProject = false;
        el.classList.remove("dragging");
      });

      // click to filter is handled elsewhere (v3.3) – keep as-is (class toggles)
      el.addEventListener("click", () => {
        // let existing handler (below) manage
      });
    });

    // Expand/collapse project workitems in sidebar
    document.querySelectorAll(".chip-expand").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const pid = btn.getAttribute("data-project-id");
        const box = pid ? document.querySelector(`.project-items[data-project-items="${pid}"]`) : null;
        const details = pid ? document.querySelector(`.proj-details[data-proj-details="${pid}"]`) : null;
        if (!box) return;
        const open = box.style.display !== "none";
        box.style.display = open ? "none" : "block";
        if (details) details.style.display = open ? "none" : "block";
        btn.classList.toggle("open", !open);
        btn.textContent = open ? "▾" : "▴";
      });
    });

    // Workitems are draggable (time-based unit planning)
    document.querySelectorAll(".workitem-chip").forEach((chip) => {
      chip.addEventListener("dragstart", (e) => {
        // Prevent bubbling up to the parent .project-chip dragstart handler
        // (otherwise it overwrites payload from workitem -> project).
        e.stopPropagation();
        const pid = chip.getAttribute("data-project-id");
        const wid = chip.getAttribute("data-workitem-id");
        setDragData(e, `workitem:${pid}:${wid}`);
        chip.classList.add("dragging");
      });
      chip.addEventListener("dragend", () => chip.classList.remove("dragging"));
    });

    // Unit tags in cells are draggable + editable (+/- 1h)
    document.querySelectorAll(".unit-tag").forEach((tag) => {
      tag.addEventListener("dragstart", (e) => {
        const id = tag.getAttribute("data-unitalloc-id");
        setDragData(e, `unit:${id}`);
        tag.classList.add("dragging");
      });
      tag.addEventListener("dragend", () => tag.classList.remove("dragging"));

      tag.addEventListener("dblclick", async () => {
        const id = tag.getAttribute("data-unitalloc-id");
        if (!id) return;
        if (!confirm("Ta bort enhetsplaneringen?")) return;
        if (await deleteUnitAlloc(id)) window.location.reload();
      });

      tag.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const id = tag.getAttribute("data-unitalloc-id");
        const curTotal = parseInt(tag.getAttribute("data-ua-total-minutes") || "120", 10);
        showCtx(e.clientX, e.clientY, (root) => {
          const title = document.createElement("div");
          title.className = "ctx-title";
          title.textContent = "Enhet";
          root.appendChild(title);

          const plus = document.createElement("div");
          plus.className = "ctx-item";
          plus.innerHTML = `<span class="ctx-dot" style="background:#22c55e"></span>+1h`;
          plus.addEventListener("click", async () => {
            hideCtx();
            const next = Math.max(60, curTotal + 60);
            if (await updateUnitAlloc(id, { minutes: next })) window.location.reload();
          });
          root.appendChild(plus);

          const minus = document.createElement("div");
          minus.className = "ctx-item";
          minus.innerHTML = `<span class="ctx-dot" style="background:#f59e0b"></span>-1h`;
          minus.addEventListener("click", async () => {
            hideCtx();
            const next = Math.max(60, curTotal - 60);
            if (await updateUnitAlloc(id, { minutes: next })) window.location.reload();
          });
          root.appendChild(minus);

          const del = document.createElement("div");
          del.className = "ctx-item";
          del.innerHTML = `<span class="ctx-dot" style="background:#ef4444"></span>Ta bort`;
          del.addEventListener("click", async () => {
            hideCtx();
            if (!confirm("Ta bort enhetsplaneringen?")) return;
            if (await deleteUnitAlloc(id)) window.location.reload();
          });
          root.appendChild(del);
        });
      });

      // Click on the hours pill to set a specific amount of hours.
      const hoursEl = tag.querySelector(".unit-hours");
      if (hoursEl) {
        hoursEl.addEventListener("click", async (e) => {
          e.preventDefault();
          e.stopPropagation();
          const id = tag.getAttribute("data-unitalloc-id");
          if (!id) return;

          const curTotal = parseInt(tag.getAttribute("data-ua-total-minutes") || "0", 10);
          const curH = (curTotal / 60.0);
          const def = (Math.round(curH * 10) / 10).toString().replace(".", ",");
          let v = prompt("Antal timmar:", def);
          if (v === null) return;
          v = (v || "").trim();
          if (!v) return;
          v = v.replace(",", ".");
          const h = parseFloat(v);
          if (!isFinite(h) || h <= 0) { alert("Ogiltigt värde."); return; }
          // Round to nearest 15 minutes for stable values
          let mins = Math.round((h * 60) / 15) * 15;
          mins = Math.max(15, mins);
          if (await updateUnitAlloc(id, { minutes: mins })) window.location.reload();
        });
      }

      // Resize hours by dragging the grip (1h steps)
      const grip = tag.querySelector(".u-resize");
      if (grip) {
        grip.addEventListener("pointerdown", (ev) => {
          ev.preventDefault();
          ev.stopPropagation();
          const id = tag.getAttribute("data-unitalloc-id");
          if (!id) return;

          const hoursEl = tag.querySelector(".unit-hours");
          const curTotal = parseInt(tag.getAttribute("data-ua-total-minutes") || "0", 10);
          let minutes = curTotal;
          let lastX = ev.clientX;
          const stepPx = 30;

          const prevDraggable = tag.draggable;
          tag.draggable = false;
          document.body.classList.add("noselect");

          function onMove(e) {
            const dx = e.clientX - lastX;
            const steps = Math.trunc(dx / stepPx);
            if (steps !== 0) {
              minutes = Math.max(15, minutes + steps * 60);
              lastX += steps * stepPx;
              if (hoursEl) hoursEl.textContent = fmtHours(minutes);
            }
          }

          async function onUp() {
            document.removeEventListener("pointermove", onMove, true);
            document.removeEventListener("pointerup", onUp, true);
            document.body.classList.remove("noselect");
            tag.draggable = prevDraggable;

            if (minutes !== curTotal) {
              if (await updateUnitAlloc(id, { minutes })) window.location.reload();
            } else if (hoursEl) {
              // restore exact value
              hoursEl.textContent = fmtHours(curTotal);
            }
          }

          document.addEventListener("pointermove", onMove, true);
          document.addEventListener("pointerup", onUp, true);
        });

        // Prevent starting a drag when grabbing the resize grip
        grip.addEventListener("dragstart", (e) => e.preventDefault());
      }


    });

    // ---------- Move + edit + delete on allocations ----------
    document.querySelectorAll(".alloc").forEach((el) => {
      const typ = (el.getAttribute("data-type") || "alloc").toLowerCase();
      const idForType = () => (typ === "adhoc" ? el.getAttribute("data-adhoc-id") : el.getAttribute("data-alloc-id"));

      el.addEventListener("dragstart", (e) => {
        // Avoid dragging when resizing
        if (el.classList.contains("resizing")) { e.preventDefault(); return; }
        const id = idForType();
        if (!id) { e.preventDefault(); return; }
        setDragData(e, (typ === "adhoc" ? "adhoc:" : "alloc:") + id);
        el.classList.add("dragging");
      });
      el.addEventListener("dragend", () => el.classList.remove("dragging"));

      el.addEventListener("dblclick", async () => {
        const id = idForType();
        if (!id) return;

        if (typ === "adhoc") {
          if (!confirm("Ta bort Fri text?")) return;
          if (await deleteAdhoc(id)) window.location.reload();
          return;
        }

        if (!confirm("Ta bort denna allokering?")) return;

        const data = {
          project_id: parseInt(el.getAttribute("data-project-id"), 10),
          person_id: parseInt((el.closest(".pt-row")?.getAttribute("data-person-row")) || el.getAttribute("data-person") || "0", 10),
          start_date: el.getAttribute("data-start"),
          end_date: el.getAttribute("data-end"),
          percent: parseInt(el.getAttribute("data-percent") || "50", 10),
        };

        const ok = await deleteAlloc(id);
        if (ok) {
          pushUndo({ t: "delete", data });
          window.location.reload();
        }
      });

      // Right-click: edit percent / delete
      el.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        e.stopPropagation();
        const id = idForType();
        if (!id) return;
        const curPct = parseInt(el.getAttribute("data-percent") || "50", 10);

        showCtx(e.clientX, e.clientY, (root) => {
          const title = document.createElement("div");
          title.className = "ctx-title";
          title.textContent = (typ === "adhoc") ? "Fri text" : "Allokering";
          root.appendChild(title);

          const edit = document.createElement("div");
          edit.className = "ctx-item";
          edit.innerHTML = `<span class="ctx-dot" style="background:#0ea5e9"></span>Ändra…`;
          edit.addEventListener("click", async () => {
            hideCtx();

            if (typ === "adhoc") {
              const curTitle = el.getAttribute("data-title") || "Småjobb";
              const title = (prompt("Rubrik:", curTitle) || "").trim() || curTitle;
              let v = prompt("Ny procent (0–200):", String(curPct));
              if (v === null) return;
              v = parseInt(v, 10);
              if (isNaN(v) || v < 0 || v > 200) { alert("Ogiltig procent."); return; }
              const ok = await updateAdhoc(id, { title, percent: v });
              if (ok) window.location.reload();
              return;
            }

            let v = prompt("Ny procent (0–200):", String(curPct));
            if (v === null) return;
            v = parseInt(v, 10);
            if (isNaN(v) || v < 0 || v > 200) { alert("Ogiltig procent."); return; }
            const prev = { percent: curPct };
            const ok = await updateAlloc(id, { percent: v });
            if (ok) {
              pushUndo({ t: "update", id, prev, next: { percent: v } });
              window.location.reload();
            }
          });
          root.appendChild(edit);

          const del = document.createElement("div");
          del.className = "ctx-item";
          del.innerHTML = `<span class="ctx-dot" style="background:#ef4444"></span>Ta bort`;
          del.addEventListener("click", async () => {
            hideCtx();
            if (!confirm((typ === "adhoc") ? "Ta bort Fri text?" : "Ta bort denna allokering?")) return;

            if (typ === "adhoc") {
              if (await deleteAdhoc(id)) window.location.reload();
              return;
            }

            const data = {
              project_id: parseInt(el.getAttribute("data-project-id"), 10),
              person_id: parseInt((el.closest(".pt-row")?.getAttribute("data-person-row")) || "0", 10),
              start_date: el.getAttribute("data-start"),
              end_date: el.getAttribute("data-end"),
              percent: parseInt(el.getAttribute("data-percent") || "50", 10),
            };

            const ok = await deleteAlloc(id);
            if (ok) {
              pushUndo({ t: "delete", data });
              window.location.reload();
            }
          });
          root.appendChild(del);

          if (typ !== "adhoc") {
            const hint = document.createElement("div");
            hint.className = "ctx-sub";
            hint.textContent = "Tips: Ctrl+Z för ångra";
            root.appendChild(hint);
          }
        });
      });
    });

    // ---------- Dropcells ----------
    document.querySelectorAll(".dropcell").forEach((cell) => {
      cell.addEventListener("dragover", (e) => {
        e.preventDefault();
        cell.classList.add("dragover");
      });
      cell.addEventListener("dragleave", () => cell.classList.remove("dragover"));
      cell.addEventListener("drop", async (e) => {
        e.preventDefault();
        cell.classList.remove("dragover");
        const payload = e.dataTransfer.getData("text/plain");
        if (!payload) return;

        const personId = cell.getAttribute("data-person");
        if (!personId) return;

        // Cell range
        const cellStart = cell.getAttribute("data-start");
        const cellEnd = cell.getAttribute("data-end");
        if (!cellStart || !cellEnd) return;

        // Range (may be multi-period). For unit planning we stay locked to the single cell.
        let start = cellStart;
        let end = cellEnd;
        if (activeRange && activeRange.personId === personId) {
          start = activeRange.start;
          end = activeRange.end;
        }
        if (!start || !end) return;

        // Unit planning: drop workitem => ask for hours and create unit allocation (time-based)
        if (payload.startsWith("workitem:")) {
          const parts = payload.split(":");
          const projectId = parts[1];
          const workItemId = parts[2];

          // Ask hours on drop (allow decimals). Normalize comma to dot.
          let hStr = prompt("Antal timmar att lägga ut?", "2");
          if (hStr === null) return;
          hStr = String(hStr).trim().replace(",", ".");
          let hours = parseFloat(hStr);
          if (!isFinite(hours) || hours <= 0) {
            alert("Ange ett giltigt antal timmar.");
            return;
          }
          // Round to nearest 15 minutes to avoid odd values.
          let minutes = Math.round(hours * 60);
          minutes = Math.max(15, Math.round(minutes / 15) * 15);

          const id = await createUnitAlloc({
            project_id: parseInt(projectId, 10),
            work_item_id: parseInt(workItemId, 10),
            person_id: parseInt(personId, 10),
            start_date: cellStart,
            end_date: cellEnd,
            minutes,
          });
          if (id) window.location.reload();
          return;
        }

        // Move unit allocation (locked to cell)
        if (payload.startsWith("unit:")) {
          const uaId = payload.split(":")[1];
          const ok = await updateUnitAlloc(uaId, {
            person_id: parseInt(personId, 10),
            start_date: cellStart,
            end_date: cellEnd,
          });
          if (ok) window.location.reload();
          return;
        }

        // Move adhoc bar (uses range if activeRange exists)
        if (payload.startsWith("adhoc:")) {
          const adhocId = payload.split(":")[1];
          let pct = null;
          if (e.shiftKey) {
            let v = prompt("Ny procent (0–200):", "25");
            if (v === null) return;
            v = parseInt(v, 10);
            if (isNaN(v) || v < 0 || v > 200) { alert("Ogiltig procent."); return; }
            pct = v;
          }
          const body = { person_id: parseInt(personId, 10), start_date: start, end_date: end };
          if (pct !== null) body.percent = pct;
          const ok = await updateAdhoc(adhocId, body);
          if (ok) window.location.reload();
          return;
        }

        if (payload.startsWith("project:")) {
          const projectId = payload.split(":")[1];
          let pct = 50;
          if (e.shiftKey) {
            let v = prompt("Procent för perioden (0–200):", "50");
            if (v === null) return;
            v = parseInt(v, 10);
            if (isNaN(v) || v < 0 || v > 200) { alert("Ogiltig procent."); return; }
            pct = v;
          }
          const id = await createAlloc(projectId, personId, start, end, pct);
          if (id) {
            pushUndo({ t: "create", id });
            window.location.reload();
          }
          return;
        }

        if (payload.startsWith("alloc:")) {
          const allocId = payload.split(":")[1];
          // Capture prev state from DOM
          const allocEl = document.querySelector(`.alloc[data-alloc-id="${allocId}"]`);
          const prev = allocEl ? {
            person_id: parseInt((allocEl.closest(".pt-row")?.getAttribute("data-person-row")) || personId, 10),
            start_date: allocEl.getAttribute("data-start"),
            end_date: allocEl.getAttribute("data-end"),
            percent: parseInt(allocEl.getAttribute("data-percent") || "50", 10),
          } : {};

          let pct = null;
          if (e.shiftKey) {
            let v = prompt("Ny procent (0–200):", String(prev.percent || 50));
            if (v === null) return;
            v = parseInt(v, 10);
            if (isNaN(v) || v < 0 || v > 200) { alert("Ogiltig procent."); return; }
            pct = v;
          }
          const body = { person_id: parseInt(personId, 10), start_date: start, end_date: end };
          if (pct !== null) body.percent = pct;

          const ok = await updateAlloc(allocId, body);
          if (ok) {
            pushUndo({ t: "update", id: allocId, prev, next: body });
            window.location.reload();
          }
          return;
        }
      });

      // Right-click: add allocation
      cell.addEventListener("contextmenu", (e) => {
        e.preventDefault();
        const personId = cell.getAttribute("data-person");
        if (!personId) return;

        let start = cell.getAttribute("data-start");
        let end = cell.getAttribute("data-end");
        if (activeRange && activeRange.personId === personId) {
          start = activeRange.start;
          end = activeRange.end;
        }
        if (!start || !end) return;
        openAddMenu(e.clientX, e.clientY, personId, start, end);
      });
    });

    // ---------- Drag-select (multi-period) ----------
    function applySelection(personId, aPi, bPi) {
      document.querySelectorAll(".dropcell.sel").forEach((el) => el.classList.remove("sel"));
      const lo = Math.min(aPi, bPi);
      const hi = Math.max(aPi, bPi);
      document.querySelectorAll(`.dropcell[data-person="${personId}"]`).forEach((cell) => {
        const pi = parseInt(cell.dataset.pi || "-1", 10);
        if (pi >= lo && pi <= hi) cell.classList.add("sel");
      });
    }

    document.addEventListener("mousedown", (e) => {
      if (e.button !== 0) return;
      if (ctx.style.display === "block") return;
      const cell = e.target.closest(".dropcell");
      if (!cell) return;
      if (e.target.closest(".alloc") || e.target.closest(".unit-tag")) return;
      if (isDraggingProject) return;

      const personId = cell.getAttribute("data-person");
      const pi = parseInt(cell.dataset.pi || "0", 10);
      selecting = { personId, startPi: pi, endPi: pi, moved: false, originX: e.clientX, originY: e.clientY };
      applySelection(personId, pi, pi);
      e.preventDefault();
    });

    document.addEventListener("mousemove", (e) => {
      if (!selecting) return;
      const cell = e.target.closest(".dropcell") || document.elementFromPoint(e.clientX, e.clientY)?.closest?.(".dropcell");
      if (!cell) return;
      if (cell.getAttribute("data-person") !== selecting.personId) return;
      const pi = parseInt(cell.dataset.pi || "0", 10);
      if (pi !== selecting.endPi) {
        selecting.endPi = pi;
        applySelection(selecting.personId, selecting.startPi, selecting.endPi);
      }
      if (Math.abs(e.clientX - selecting.originX) + Math.abs(e.clientY - selecting.originY) > 6) {
        selecting.moved = true;
      }
    });

    document.addEventListener("mouseup", (e) => {
      if (!selecting) return;
      const { personId, startPi, endPi, moved } = selecting;
      const lo = Math.min(startPi, endPi);
      const hi = Math.max(startPi, endPi);

      // commit range
      const d = datesForRange(personId, lo, hi);
      selecting = null;

      // open menu only if user actually dragged (moved), otherwise treat as click
      if (moved && d && d.start && d.end) {
        activeRange = { personId, lo, hi, start: d.start, end: d.end };
        // anchor to last selected cell
        const anchor = document.querySelector(`.dropcell[data-person="${personId}"][data-pi="${hi}"]`) ||
                       document.querySelector(`.dropcell[data-person="${personId}"]`);
        let x = e.clientX, y = e.clientY;
        if (anchor && anchor.getBoundingClientRect) {
          const r = anchor.getBoundingClientRect();
          x = r.left + Math.min(160, Math.max(40, r.width * 0.65));
          y = r.top + 16;
        }
        setTimeout(() => openAddMenu(x, y, personId, d.start, d.end), 0);
      } else {
        clearSelection();
      }
    });

    // ---------- Resize handles ----------
    let resize = null;

    function beginResize(e, side, allocEl) {
      e.preventDefault();
      e.stopPropagation();
      const typ = (allocEl.getAttribute("data-type") || "alloc").toLowerCase();
      const allocId = (typ === "adhoc") ? allocEl.getAttribute("data-adhoc-id") : allocEl.getAttribute("data-alloc-id");
      const row = allocEl.closest(".pt-row");
      const personId = row ? row.getAttribute("data-person-row") : null;
      if (!allocId || !personId) return;

      const prev = {
        person_id: parseInt(personId, 10),
        start_date: allocEl.getAttribute("data-start"),
        end_date: allocEl.getAttribute("data-end"),
        percent: parseInt(allocEl.getAttribute("data-percent") || "50", 10),
      };

      // derive initial pis from alloc (snap-to-view). Fallback: try matching dates.
      let startPi = parseInt(allocEl.getAttribute("data-start-pi") || "0", 10);
      let endPi = parseInt(allocEl.getAttribute("data-end-pi") || "0", 10);
      if (isNaN(startPi) || isNaN(endPi)) {
        startPi = 0; endPi = 0;
        const cells = Array.from(document.querySelectorAll(`.dropcell[data-person="${personId}"]`));
        cells.forEach((c) => {
          const pi = parseInt(c.dataset.pi || "0", 10);
          if (c.getAttribute("data-start") === prev.start_date) startPi = pi;
          if (c.getAttribute("data-end") === prev.end_date) endPi = pi;
        });
      }

      resize = { side, allocId, typ, personId, startPi, endPi, prev };
      allocEl.classList.add("resizing");
      applySelection(personId, startPi, endPi);

      const move = (ev) => {
        if (!resize) return;
        const el = document.elementFromPoint(ev.clientX, ev.clientY);
        const cell = el && el.closest ? el.closest(".dropcell") : null;
        if (!cell) return;
        if (cell.getAttribute("data-person") !== resize.personId) return;
        const pi = parseInt(cell.dataset.pi || "0", 10);
        if (resize.side === "left") resize.startPi = Math.min(pi, resize.endPi);
        else resize.endPi = Math.max(pi, resize.startPi);
        applySelection(resize.personId, resize.startPi, resize.endPi);
      };

      const up = async () => {
        document.removeEventListener("pointermove", move, true);
        document.removeEventListener("pointerup", up, true);

        const allocNow = (typ === "adhoc")
          ? document.querySelector(`.alloc[data-adhoc-id="${allocId}"]`)
          : document.querySelector(`.alloc[data-alloc-id="${allocId}"]`);
        if (allocNow) allocNow.classList.remove("resizing");

        const d = datesForRange(personId, resize.startPi, resize.endPi);
        if (!d) { clearSelection(); resize = null; return; }

        const body = { start_date: d.start, end_date: d.end };
        const changed = (d.start !== prev.start_date) || (d.end !== prev.end_date);
        resize = null;

        if (!changed) { clearSelection(); return; }

        const ok = (typ === "adhoc") ? await updateAdhoc(allocId, body) : await updateAlloc(allocId, body);
        if (ok) {
          if (typ !== "adhoc") pushUndo({ t: "update", id: allocId, prev, next: body });
          window.location.reload();
        } else {
          clearSelection();
        }
      };

      document.addEventListener("pointermove", move, true);
      document.addEventListener("pointerup", up, true);
    }

    document.querySelectorAll(".alloc-handle.left").forEach((h) => {
      h.addEventListener("pointerdown", (e) => beginResize(e, "left", e.target.closest(".alloc")));
    });
    document.querySelectorAll(".alloc-handle.right").forEach((h) => {
      h.addEventListener("pointerdown", (e) => beginResize(e, "right", e.target.closest(".alloc")));
    });

    // ---------- Sidebar collapse ----------
    const toggleSidebar = document.getElementById("toggleSidebar");
    const layout = document.getElementById("layout");
    if (toggleSidebar && layout) {
      toggleSidebar.addEventListener("click", () => layout.classList.toggle("collapsed"));
    }

    // ---------- Filters (kept from v3.3) ----------
    const personSearch = document.getElementById("personSearch");
    const projectSearch = document.getElementById("projectSearch");
    const overOnly = document.getElementById("overOnly");
    const clearFiltersBtn = document.getElementById("clearFilters");

    const selectedProjects = new Set();
    function norm(s) { return String(s || "").toLowerCase().trim(); }

    function applyProjectFading() {
      const active = selectedProjects.size > 0;
      // Only fade project allocations + unit planning (not ad-hoc)
      document.querySelectorAll('.alloc[data-type="alloc"]').forEach((a) => {
        const pid = a.getAttribute("data-project-id");
        a.classList.toggle("faded", active && !selectedProjects.has(pid));
      });

      document.querySelectorAll('.unit-tag').forEach((t) => {
        const pid = t.getAttribute('data-project-id');
        t.classList.toggle('faded', active && !selectedProjects.has(pid));
      });

      document.querySelectorAll('.workitem-chip').forEach((t) => {
        const pid = t.getAttribute('data-project-id');
        t.classList.toggle('faded', active && !selectedProjects.has(pid));
      });
      document.querySelectorAll(".project-chip").forEach((chip) => {
        const pid = chip.getAttribute("data-project-id");
        chip.classList.toggle("fsel", selectedProjects.has(pid));
      });
    }

    function personIsOverbooked(pid) {
      const q = `.pt-row[data-person-row="${pid}"] .pct-over`;
      return Boolean(document.querySelector(q));
    }

    function getPersonName(pid) {
      const link = document.querySelector(`.pt-person-cell[data-person-row="${pid}"] .person-link`);
      return link ? link.textContent : "";
    }

    function setPersonVisible(pid, visible) {
      document.querySelectorAll(`[data-person-row="${pid}"]`).forEach((el) => {
        el.style.display = visible ? "" : "none";
      });
    }

    function applyFilters() {
      const personTerm = norm(personSearch?.value || "");
      const projTerm = norm(projectSearch?.value || "");
      const onlyOver = Boolean(overOnly?.checked);

      // filter projects list
      document.querySelectorAll(".project-chip").forEach((chip) => {
        const name = norm(chip.querySelector(".chip-title")?.textContent || "");
        const visible = !projTerm || name.includes(projTerm);
        chip.style.display = visible ? "" : "none";
      });

      // filter persons rows
      const personIds = Array.from(document.querySelectorAll(".pt-person-cell[data-person-row]"))
        .map((el) => el.getAttribute("data-person-row"));

      personIds.forEach((pid) => {
        const name = norm(getPersonName(pid));
        let visible = (!personTerm || name.includes(personTerm));
        if (visible && onlyOver) visible = personIsOverbooked(pid);
        setPersonVisible(pid, visible);
      });

      applyProjectFading();
    }

    document.querySelectorAll(".project-chip").forEach((chip) => {
      chip.addEventListener("click", (e) => {
        const pid = chip.getAttribute("data-project-id");
        if (!pid) return;
        if (selectedProjects.has(pid)) selectedProjects.delete(pid);
        else selectedProjects.add(pid);
        applyProjectFading();
      });
    });

    if (personSearch) personSearch.addEventListener("input", applyFilters);
    if (projectSearch) projectSearch.addEventListener("input", applyFilters);
    if (overOnly) overOnly.addEventListener("change", applyFilters);
    if (clearFiltersBtn) {
      clearFiltersBtn.addEventListener("click", () => {
        if (personSearch) personSearch.value = "";
        if (projectSearch) projectSearch.value = "";
        if (overOnly) overOnly.checked = false;
        selectedProjects.clear();
        applyFilters();
      });
    }

    applyFilters();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }
})();