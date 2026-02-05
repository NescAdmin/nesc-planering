(function () {
  let draggedForm = null;

  document.querySelectorAll("form.block").forEach(el => {
    el.addEventListener("dragstart", (e) => {
      draggedForm = el;
      e.dataTransfer.setData("text/plain", el.getAttribute("data-block"));
    });
  });

  document.querySelectorAll("td.dropcell").forEach(cell => {
    cell.addEventListener("dragover", (e) => {
      e.preventDefault();
      cell.classList.add("dragover");
    });
    cell.addEventListener("dragleave", () => cell.classList.remove("dragover"));
    cell.addEventListener("drop", (e) => {
      e.preventDefault();
      cell.classList.remove("dragover");
      if (!draggedForm) return;

      const personId = cell.getAttribute("data-person");
      const day = cell.getAttribute("data-day");

      // submit move form to backend
      const form = draggedForm;
      form.querySelector("input[name=person_id]").value = personId;
      form.querySelector("input[name=day]").value = day;
      form.submit();
    });
  });
})();
