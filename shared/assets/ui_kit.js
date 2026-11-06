/* Shared workspace behaviour (shared/ui_kit.py): filter pills and help pop-overs only.
   - One pill or help pop-over open at a time; a click outside or Escape closes it.
   - A pill marked data-autoclose closes once its single choice changes.
   - A pill face's .ws-fpill-val[data-for] shows the current choice of that input (radio group, select or
     selectize, numeric); an empty choice shows data-empty and is not marked as narrowed (.set), nor is a plain selection
     (data-plain) or an amount (data-unit). */
(function () {
  if (window.__wsKit) { return; }
  window.__wsKit = true;
  var POPS = 'details.ws-fpill[open], details.ws-help[open]';

  function closeOthers(keep) {
    document.querySelectorAll(POPS).forEach(function (box) { if (box !== keep) { box.removeAttribute('open'); } });
  }
  document.addEventListener('toggle', function (event) {
    var box = event.target;
    if (!box.classList || !box.open || !(box.classList.contains('ws-fpill') || box.classList.contains('ws-help'))) { return; }
    closeOthers(box);
    var first = box.querySelector('.ws-fpop input[type="search"], .ws-fpop .selectize-input input');
    if (first) { setTimeout(function () { first.focus(); }, 0); }
  }, true);
  document.addEventListener('click', function (event) {
    document.querySelectorAll(POPS).forEach(function (box) {
      if (!box.contains(event.target)) { box.removeAttribute('open'); }
    });
  });
  document.addEventListener('keydown', function (event) {
    if (event.key !== 'Escape') { return; }
    document.querySelectorAll(POPS).forEach(function (box) {
      box.removeAttribute('open');
      var face = box.querySelector('summary');
      if (face) { face.focus(); }
    });
  });

  function choiceText(id) {
    var el = document.getElementById(id);
    if (!el) { return null; }
    if (el.tagName === 'SELECT') {
      var option = el.options[el.selectedIndex];
      return option && option.value !== '' ? option.text : '';
    }
    if (el.classList.contains('shiny-input-radiogroup')) {
      var checked = el.querySelector('input:checked');
      return checked && checked.value !== '' ? checked.parentNode.textContent.trim() : '';
    }
    if (el.classList.contains('shiny-input-checkboxgroup')) {
      var all = el.querySelectorAll('input[type="checkbox"]'), on = el.querySelectorAll('input:checked');
      return on.length === 0 || on.length === all.length ? '' : (on.length === 1 ? on[0].parentNode.textContent.trim()
        : on.length + ' of ' + all.length);
    }
    return el.value == null ? '' : String(el.value);
  }
  function sync(id) {
    document.querySelectorAll('.ws-fpill-val[data-for]').forEach(function (face) {
      if (id && face.getAttribute('data-for') !== id) { return; }
      var text = choiceText(face.getAttribute('data-for'));
      if (text === null) { return; }
      var unit = face.getAttribute('data-unit');
      face.textContent = text === '' ? (face.getAttribute('data-empty') || 'All') : text + (unit ? ' ' + unit : '');
      face.classList.toggle('set', text !== '' && !face.hasAttribute('data-unit') && !face.hasAttribute('data-plain'));
    });
  }
  window.wsPillSync = sync;
  var $ = window.jQuery;
  if (!$) { return; }
  $(document).on('shiny:inputchanged', function (event) {
    setTimeout(function () { sync(event.name); }, 0);
    var input = document.getElementById(event.name);
    var pill = input && input.closest && input.closest('details.ws-fpill[data-autoclose][open]');
    if (pill && event.priority !== 'deferred') { pill.removeAttribute('open'); }
  });
  $(document).on('shiny:connected shiny:value shiny:bound', function () { setTimeout(function () { sync(); }, 0); });
})();
