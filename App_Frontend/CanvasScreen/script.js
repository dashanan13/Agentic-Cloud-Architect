let zoom = 100;

function zoomIn() {
  zoom += 10;
  updateZoom();
}

function zoomOut() {
  zoom -= 10;
  if (zoom < 10) zoom = 10;
  updateZoom();
}

function updateZoom() {
  document.getElementById("zoomValue").innerText = "Zoom: " + zoom + "%";
}

function resetCanvas() {
  document.getElementById("infoMessage").innerText = "Canvas reset";
}

function switchTab(event, tabId) {
  // remove active from all tabs
  document.querySelectorAll(".tab").forEach(tab => tab.classList.remove("active"));
  document.querySelectorAll(".tab-pane").forEach(pane => pane.classList.remove("active"));

  // activate selected
  event.target.classList.add("active");
  document.getElementById(tabId).classList.add("active");
}