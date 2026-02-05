(function () {
  let draggedBlockId = null;

  function bindEvents() {
    document.querySelectorAll(".event[draggable='true']").forEach(el => {
      el.addEventListener("dragstart", (e) => {
        draggedBlockId = el.getAttribute("data-block-id");
        e.dataTransfer.setData("text/plain", draggedBlockId);
        e.dataTransfer.effectAllowed = "move";
      });
    });

    document.querySelectorAll(".slot").forEach(slot => {
      slot.addEventListener("dragover", (e) => {
        e.preventDefault();
        slot.classList.add("dragover");
      });
      slot.addEventListener("dragleave", () => slot.classList.remove("dragover"));
      slot.addEventListener("drop", async (e) => {
        e.preventDefault();
        slot.classList.remove("dragover");

        const id = draggedBlockId || e.dataTransfer.getData("text/plain");
        if (!id) return;

        const personId = slot.getAttribute("data-person");
        const start = slot.getAttribute("data-start");

        const fd = new FormData();
        fd.append("person_id", personId);
        fd.append("start", start);

        try {
          const res = await fetch(`/blocks/${id}/move2`, {
            method: "POST",
            body: fd
          });
          if (!res.ok) {
            const txt = await res.text();
            alert(`Kunde inte flytta block (HTTP ${res.status}).\n${txt}`);
            return;
          }
          window.location.reload();
        } catch (err) {
          alert(`Nätverksfel: ${err}`);
        }
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindEvents);
  } else {
    bindEvents();
  }
})();
