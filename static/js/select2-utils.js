/* Select2 init shared by every "search an account" dropdown.
 *
 * Every account-picker form had its own copy of the same select2 config, and
 * three of them (verifikationsmallar, verifikationer, leverantörsfakturor)
 * also duplicated a "reopen the dropdown when its selection box gets focus"
 * keyboard handler byte-for-byte. Both live here instead.
 */
window.SaldoVibe = window.SaldoVibe || {};

(function (namespace) {
  'use strict';

  function initAccountSelect($select, placeholder) {
    if ($select.hasClass('select2-hidden-accessible')) {
      return $select;
    }
    $select.select2({
      placeholder: placeholder || 'Sök konto...',
      allowClear: true,
      width: '100%',
      language: 'sv',
    });
    // Native required-validation anchors an English browser tooltip to the
    // select2-hidden <select> — or silently blocks the submit with no feedback
    // at all. Serverns svenska validering tar över istället.
    $select.removeAttr('required');
    return $select;
  }

  function bindReopenOnFocus($select) {
    const $container = $select.next('.select2-container');
    const $selection = $container.find('.select2-selection');

    $select.off('select2:open.accountkeyboard');
    $select.on('select2:open.accountkeyboard', function () {
      const searchField = document.querySelector('.select2-container--open .select2-search__field');
      if (searchField) {
        searchField.focus();
      }
    });

    $selection.off('focus.accountkeyboard');
    $selection.on('focus.accountkeyboard', function () {
      if (!$container.hasClass('select2-container--open')) {
        $select.select2('open');
      }
    });
  }

  namespace.select2Utils = {
    initAccountSelect: initAccountSelect,
    bindReopenOnFocus: bindReopenOnFocus,
  };
})(window.SaldoVibe);
