/* 다이어그램 페이지의 버전 드롭다운 — 뷰어(v2 iframe ↔ v1 이미지)와
   "전체 화면으로 열기" 링크를 선택한 버전으로 전환한다. 기본 v2. */
document.addEventListener("DOMContentLoaded", function () {
  document.querySelectorAll(".ep-version-select").forEach(function (sel) {
    var meta = sel.closest(".ep-diagram-meta");
    var link = meta && meta.querySelector(".ep-open-full");
    var frame = meta && meta.nextElementSibling;
    if (!link || !frame || !frame.classList.contains("ep-diagram-frame")) return;
    var hrefs = { v2: link.getAttribute("href"), v1: link.getAttribute("data-href-v1") };
    var apply = function () {
      frame.querySelectorAll("[data-version]").forEach(function (el) {
        el.hidden = el.dataset.version !== sel.value;
      });
      if (hrefs[sel.value]) link.setAttribute("href", hrefs[sel.value]);
    };
    sel.addEventListener("change", apply);
    apply(); // 새로고침 시 브라우저가 복원한 폼 값과 뷰어를 동기화
  });
});
