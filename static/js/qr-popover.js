/* Shared hover/click popover for showing a payment QR code (leverantörsfaktura list + detail). */
window.SaldoVibe = window.SaldoVibe || {};

(function (namespace) {
  'use strict';

  function initQrPopovers(selector) {
    var triggers = document.querySelectorAll(selector || '.qr-hover-trigger');
    var isTouchDevice = window.matchMedia('(hover: none), (pointer: coarse)').matches;

    triggers.forEach(function (el) {
      var qrUrl = el.getAttribute('data-qr-url');
      if (!qrUrl) {
        return;
      }

      var qrImageSize = isTouchDevice ? 280 : 320;
      new bootstrap.Popover(el, {
        trigger: isTouchDevice ? 'click' : 'hover focus',
        placement: 'left',
        html: true,
        sanitize: false,
        customClass: 'invoice-qr-popover',
        content: '<img src="' + qrUrl + '" alt="Betalnings-QR" style="width:' + qrImageSize + 'px;height:' + qrImageSize + 'px;display:block;">',
      });

      if (isTouchDevice) {
        el.addEventListener('click', function (event) {
          event.preventDefault();
        });
      }
    });
  }

  namespace.qrPopover = { init: initQrPopovers };
})(window.SaldoVibe);
