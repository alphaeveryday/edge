/* 다이어그램 페이지의 버전 드롭다운 — 뷰어·설계 근거 노트와 "전체 화면으로 열기" 링크를
   선택한 버전으로 전환한다. 기본은 selected 옵션(1~4번은 v2, 5번은 v5). */
document.addEventListener("DOMContentLoaded", function () {
  document.querySelectorAll(".ep-version-select").forEach(function (sel) {
    var meta = sel.closest(".ep-diagram-meta");
    var link = meta && meta.querySelector(".ep-open-full");
    var frame = meta && meta.nextElementSibling;
    if (!link || !frame || !frame.classList.contains("ep-diagram-frame")) return;
    var scopes = [frame];
    var note = frame.nextElementSibling;
    if (note && note.classList && note.classList.contains("ep-version-note")) scopes.push(note);
    // 옵션별 전체 화면 링크 — 기본 선택은 href, 나머지는 data-href-<값>(2판 이상도 같은 규칙)
    var hrefs = {};
    Array.prototype.forEach.call(sel.options, function (o) {
      hrefs[o.value] = link.getAttribute("data-href-" + o.value) || link.getAttribute("href");
    });
    var apply = function () {
      scopes.forEach(function (scope) {
        scope.querySelectorAll("[data-version]").forEach(function (el) {
          el.hidden = el.dataset.version !== sel.value;
        });
      });
      if (hrefs[sel.value]) link.setAttribute("href", hrefs[sel.value]);
    };
    sel.addEventListener("change", apply);
    apply(); // 새로고침 시 브라우저가 복원한 폼 값과 뷰어를 동기화
  });
});
