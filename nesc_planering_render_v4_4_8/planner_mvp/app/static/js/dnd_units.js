(function () {
  function qs(sel, el) { return (el || document).querySelector(sel); }
  function qsa(sel, el) { return Array.from((el || document).querySelectorAll(sel)); }

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

  function _withAllowOver(url){
    const sep = url.includes('?') ? '&' : '?';
    return url + sep + 'allow_over=true';
  }

  async function _fetchWithScope(url, opts){
    let res = await fetch(url, opts);
    if (res.status === 409){
      let js = null;
      try { js = await res.json(); } catch(e) {}
      const ok = await _confirmScopeExceeded(js);
      if (!ok) return { cancelled: true, res };
      res = await fetch(_withAllowOver(url), opts);
    }
    return { cancelled: false, res };
  }

  const projectId = (window.__project || {}).id;
  const workitems = (window.__workitems || []).slice();
  let activeWorkItemId = workitems.length ? workitems[0].id : null;

  // Mark active unit item
  function setActiveWorkItem(id) {
    activeWorkItemId = id;
    qsa(".unit-item").forEach(x => x.classList.toggle("active", Number(x.dataset.workitemId) === Number(id)));
  }
  qsa(".unit-item").forEach(item => {
    item.addEventListener("click", () => setActiveWorkItem(item.dataset.workitemId));
    item.addEventListener("dragstart", (e) => {
      e.dataTransfer.setData("text/plain", "WI:" + item.dataset.workitemId);
      e.dataTransfer.effectAllowed = "copy";
      setActiveWorkItem(item.dataset.workitemId);
    });
  });
  if (activeWorkItemId) setActiveWorkItem(activeWorkItemId);

  // ---------- Context menu / popup ----------
  let menuEl = null;
  function closeMenu() {
    if (menuEl) menuEl.remove();
    menuEl = null;
  }

  function openCreateMenu(anchorRect, payload) {
    closeMenu();
    const wiOptions = workitems.map(w => `<option value="${w.id}" ${Number(w.id)===Number(payload.work_item_id)?"selected":""}>${w.title}</option>`).join("");
    menuEl = document.createElement("div");
    menuEl.className = "ctx-menu";
    menuEl.style.left = (anchorRect.left) + "px";
    menuEl.style.top = (anchorRect.bottom + 6) + "px";
    menuEl.innerHTML = `
      <div class="ctx-title">Lägg till enheter</div>
      <div class="ctx-row">
        <label>Enhet</label>
        <select id="uaWi">${wiOptions}</select>
      </div>
      <div class="ctx-row">
        <label>Antal</label>
        <input id="uaQty" type="number" min="1" value="1" style="width:100%"/>
      </div>
      <div class="ctx-row">
        <button class="btn primary" id="uaCreate">Skapa</button>
        <button class="btn" id="uaCancel">Avbryt</button>
      </div>
      <div class="muted" style="margin-top:8px">Period: ${payload.start_date} → ${payload.end_date}</div>
    `;
    document.body.appendChild(menuEl);

    qs("#uaCancel", menuEl).onclick = closeMenu;
    qs("#uaCreate", menuEl).onclick = async () => {
      const wid = Number(qs("#uaWi", menuEl).value);
      const qty = Number(qs("#uaQty", menuEl).value || 0);
      if (!wid || qty <= 0) return;      const baseUrl = `/api/unit_allocations?project_id=${encodeURIComponent(projectId)}&work_item_id=${encodeURIComponent(wid)}&person_id=${encodeURIComponent(payload.person_id)}&start_date=${encodeURIComponent(payload.start_date)}&end_date=${encodeURIComponent(payload.end_date)}&quantity=${encodeURIComponent(qty)}`;
      const out = await _fetchWithScope(baseUrl, { method: "POST" });
      if (out.cancelled) return;
      const res = out.res;
      if (!res.ok){
        alert("Kunde inte skapa (HTTP " + res.status + ")");
        return;
      }
      location.reload();
    };

    // close on outside click
    setTimeout(() => {
      const onDoc = (e) => {
        if (menuEl && !menuEl.contains(e.target)) closeMenu();
      };
      document.addEventListener("mousedown", onDoc, { once: true });
    }, 0);
  }

  // ---------- Range selection ----------
  let selecting = false;
  let selStart = null;
  let selEnd = null;
  let selPersonId = null;

  function cellIndex(cell) {
    const row = cell.closest(".pt-bg");
    const cells = qsa(".pt-cell", row);
    return cells.indexOf(cell);
  }

  function updateSelection() {
    if (!selecting || !selStart || !selEnd) return;
    const row = selStart.closest(".pt-bg");
    const cells = qsa(".pt-cell", row);
    const a = cellIndex(selStart);
    const b = cellIndex(selEnd);
    const lo = Math.min(a, b), hi = Math.max(a, b);
    cells.forEach((c, i) => c.classList.toggle("dragover", i >= lo && i <= hi));
  }

  function clearSelection() {
    qsa(".pt-cell.dragover").forEach(c => c.classList.remove("dragover"));
    selecting = false; selStart = selEnd = null; selPersonId = null;
  }

  qsa(".pt-cell").forEach(cell => {
    cell.addEventListener("mousedown", (e) => {
      if (e.button !== 0) return; // left
      closeMenu();
      selecting = true;
      selStart = cell;
      selEnd = cell;
      selPersonId = Number(cell.dataset.personId);
      updateSelection();
      e.preventDefault();
    });
    cell.addEventListener("mouseenter", () => {
      if (!selecting) return;
      selEnd = cell;
      updateSelection();
    });
    cell.addEventListener("mouseup", (e) => {
      if (!selecting) return;
      selEnd = cell;
      updateSelection();
      // open menu anchored at end cell
      const row = selStart.closest(".pt-bg");
      const cells = qsa(".pt-cell", row);
      const a = cellIndex(selStart);
      const b = cellIndex(selEnd);
      const lo = Math.min(a, b), hi = Math.max(a, b);
      const startCell = cells[lo];
      const endCell = cells[hi];
      const payload = {
        project_id: projectId,
        person_id: selPersonId,
        work_item_id: Number(activeWorkItemId),
        start_date: startCell.dataset.periodStart,
        end_date: endCell.dataset.periodEnd
      };
      openCreateMenu(endCell.getBoundingClientRect(), payload);
      selecting = false;
    });

    // Right click to add on single cell
    cell.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      const payload = {
        project_id: projectId,
        person_id: Number(cell.dataset.personId),
        work_item_id: Number(activeWorkItemId),
        start_date: cell.dataset.periodStart,
        end_date: cell.dataset.periodEnd
      };
      openCreateMenu(cell.getBoundingClientRect(), payload);
    });

    // Drag-drop unit item into cell uses current active workitem
    cell.addEventListener("dragover", (e) => {
      const d = (e.dataTransfer && e.dataTransfer.getData("text/plain")) || "";
      if (d.startsWith("WI:")) e.preventDefault();
    });
    cell.addEventListener("drop", (e) => {
      const d = (e.dataTransfer && e.dataTransfer.getData("text/plain")) || "";
      if (!d.startsWith("WI:")) return;
      e.preventDefault();
      const wid = Number(d.slice(3));
      setActiveWorkItem(wid);
      const payload = {
        project_id: projectId,
        person_id: Number(cell.dataset.personId),
        work_item_id: wid,
        start_date: cell.dataset.periodStart,
        end_date: cell.dataset.periodEnd
      };
      openCreateMenu(cell.getBoundingClientRect(), payload);
    });
  });

  document.addEventListener("mouseup", (e) => {
    if (selecting) {
      // if mouseup outside, clear
      clearSelection();
    }
  });

  // ---------- Move / delete (basic) ----------
  qsa(".pt-alloc").forEach(bar => {
    bar.draggable = true;
    bar.addEventListener("dragstart", (e) => {
      e.dataTransfer.setData("text/plain", "UA:" + bar.dataset.unitallocId);
      e.dataTransfer.effectAllowed = "move";
    });
    bar.addEventListener("contextmenu", async (e) => {
      e.preventDefault();
      if (!confirm("Ta bort denna enhetsplanering?")) return;
      await fetch(`/api/unit_allocations/${bar.dataset.unitallocId}`, { method: "DELETE" });
      location.reload();
    });
  });

  // Allow dropping bars onto cells to move to new person/period start (keeps duration in periods)
  qsa(".pt-cell").forEach(cell => {
    cell.addEventListener("dragover", (e) => {
      const d = (e.dataTransfer && e.dataTransfer.getData("text/plain")) || "";
      if (d.startsWith("UA:")) e.preventDefault();
    });
    cell.addEventListener("drop", async (e) => {
      const d = (e.dataTransfer && e.dataTransfer.getData("text/plain")) || "";
      if (d.startsWith("UA_RESIZE:")) {
        const parts = d.split(":");
        const side = parts[1];
        const uaId = Number(parts[2]);
        const bar = qs(`.pt-alloc[data-unitalloc-id="${uaId}"]`);
        if (!bar) return;
        const startPi = Number(bar.dataset.startPi);
        const endPi = Number(bar.dataset.endPi);
        const row = cell.closest(".pt-row");
        const bg = row.querySelector(".pt-bg");
        const cells = qsa(".pt-cell", bg);
        const idx = cells.indexOf(cell);
        let newStartPi = startPi;
        let newEndPi = endPi;
        if (side === "left") newStartPi = Math.min(idx, endPi);
        if (side === "right") newEndPi = Math.max(idx, startPi);
        const start_date = cells[newStartPi].dataset.periodStart;
        const end_date = cells[newEndPi].dataset.periodEnd;
        const url = `/api/unit_allocations/${uaId}?start_date=${encodeURIComponent(start_date)}&end_date=${encodeURIComponent(end_date)}`;
        const out = await _fetchWithScope(url, { method: "PUT" });
        if (out.cancelled) return;
        if (!out.res.ok){
          alert("Kunde inte uppdatera (HTTP " + out.res.status + ")");
          return;
        }
        location.reload();
        return;
      }
      if (!d.startsWith("UA:")) return;
      e.preventDefault();
      const uaId = Number(d.slice(3));
      // Use current cell as new start; keep same span length in periods
      const bar = qs(`.pt-alloc[data-unitalloc-id="${uaId}"]`);
      if (!bar) return;
      const span = Number(bar.dataset.endPi) - Number(bar.dataset.startPi);
      const row = cell.closest(".pt-row");
      const bg = row.querySelector(".pt-bg");
      const cells = qsa(".pt-cell", bg);
      const startIdx = cells.indexOf(cell);
      const endIdx = Math.min(cells.length - 1, startIdx + span);
      const start_date = cells[startIdx].dataset.periodStart;
      const end_date = cells[endIdx].dataset.periodEnd;
      const person_id = Number(cell.dataset.personId);
      const url = `/api/unit_allocations/${uaId}?person_id=${encodeURIComponent(person_id)}&start_date=${encodeURIComponent(start_date)}&end_date=${encodeURIComponent(end_date)}`;
      const out = await _fetchWithScope(url, { method: "PUT" });
      if (out.cancelled) return;
      if (!out.res.ok){
        alert("Kunde inte uppdatera (HTTP " + out.res.status + ")");
        return;
      }
      location.reload();
    });
  });


  // Resize handles: drag handle and drop on a cell to set new start/end
  qsa(".resize-handle").forEach(h => {
    h.draggable = true;
    h.addEventListener("dragstart", (e) => {
      e.stopPropagation();
      const bar = h.closest(".pt-alloc");
      if (!bar) return;
      const uaId = bar.dataset.unitallocId;
      const side = h.dataset.handle;
      e.dataTransfer.setData("text/plain", "UA_RESIZE:" + side + ":" + uaId);
      e.dataTransfer.effectAllowed = "move";
    });
  });

})();