$(document).ready(function() {

    // 0. CSRF Token Setup for AJAX (Optional, usually handled by form.serialize())
    // var csrfToken = $('meta[name="csrf-token"]').attr('content'); 
    // $.ajaxSetup({
    //     beforeSend: function(xhr, settings) {
    //         if (!/^(GET|HEAD|OPTIONS|TRACE)$/i.test(settings.type) && !this.crossDomain) {
    //             xhr.setRequestHeader("X-CSRFToken", csrfToken);
    //         }
    //     }
    // });

    // Toast helper (replaces alert() popups). Usage: showToast('Saved', 'success'|'danger'|'info')
    window.showToast = function(message, type) {
        var icons = {success: 'fa-check-circle', danger: 'fa-exclamation-circle', info: 'fa-info-circle'};
        type = icons[type] ? type : 'info';
        var $stack = $('#toast-stack');
        if (!$stack.length) { $stack = $('<div id="toast-stack" aria-live="polite"></div>').appendTo('body'); }
        var $t = $('<div class="toast-msg toast-' + type + '" role="status"></div>')
            .append($('<i class="fas ' + icons[type] + '"></i>'))
            .append($('<span></span>').text(message));
        $stack.append($t);
        setTimeout(function() { $t.addClass('show'); }, 10);
        setTimeout(function() { $t.removeClass('show'); setTimeout(function() { $t.remove(); }, 300); }, 3200);
    };

    // 1. AJAX Add to Cart (targets forms with class 'add-to-cart-form-js')
    $('body').on('submit', '.add-to-cart-form-js', function(e) {
        e.preventDefault();
        var form = $(this);
        var btn = form.find('button[type=submit]').prop('disabled', true);
        $.ajax({
            type: "POST",
            url: form.attr('action'),
            data: form.serialize(),
            success: function(response) {
                if (response && response.success) {
                    $('#cart-count').text(response.count);
                    showToast(response.message || 'Added to cart', 'success');
                } else if (typeof response === 'string') {
                    // Not logged in: the server redirected us to the login page
                    showToast('Please log in to continue.', 'info');
                    setTimeout(function() { window.location = '/login'; }, 1200);
                } else {
                    showToast((response && response.message) || 'Could not process item.', 'danger');
                }
            },
            error: function(xhr) {
                if (xhr.status === 401) {
                    // Guest: go to login and come back to this page afterwards
                    showToast('Please log in to add items to your cart.', 'info');
                    setTimeout(function() { window.location = '/login?next=' + encodeURIComponent(window.location.pathname + window.location.search); }, 1000);
                    return;
                }
                var msg = (xhr.responseJSON && xhr.responseJSON.message) ||
                          (xhr.status === 400 ? 'Session expired - please refresh the page.' : 'Something went wrong. Please try again.');
                showToast(msg, 'danger');
            },
            complete: function() { btn.prop('disabled', false); }
        });
    });

    // 2. M-Pesa Details Toggle on Checkout Page
    if ($('input[name="payment_method"]').length && $('#mpesa-details').length) {
        const paymentMethodRadios = document.querySelectorAll('input[name="payment_method"]');
        const mpesaDetailsDiv = document.getElementById('mpesa-details');
        const mpesaPhoneNumberInput = document.querySelector('input[name="phone_number"]');
        function toggleMpesaDetails() {
            const selectedPaymentMethod = document.querySelector('input[name="payment_method"]:checked');
            if (selectedPaymentMethod && selectedPaymentMethod.value === 'Lipa na M-Pesa') {
                $(mpesaDetailsDiv).slideDown(); // Use jQuery slideDown for smoother effect
                // optional: empty means "use the contact phone"
            } else {
                $(mpesaDetailsDiv).slideUp(); // Use jQuery slideUp
                if (mpesaPhoneNumberInput) mpesaPhoneNumberInput.required = false;
            }
        }
        paymentMethodRadios.forEach(function (radio) { radio.addEventListener('change', toggleMpesaDetails); });
        toggleMpesaDetails(); // Initial check
    }

    // 3. Admin Sidebar Toggle with Hover-to-Show (for admin_layout.html)
    var $adminSidebar = $('#admin-sidebar');
    var $sidebarCollapseButton = $('#sidebarCollapse');
    var $sidebarHoverZone = $('#sidebar-hover-zone');

    if ($sidebarCollapseButton.length && $adminSidebar.length && $sidebarHoverZone.length) {
        
        // Function to show sidebar
        function showSidebar() {
            $adminSidebar.removeClass('hidden-by-click').addClass('hover-active');
        }

        // Function to hide sidebar (if it was shown by hover)
        function hideSidebarFromHover() {
            if ($adminSidebar.hasClass('hover-active') && !$adminSidebar.data('pinned-open')) {
                 $adminSidebar.removeClass('hover-active').addClass('hidden-by-click');
            }
        }
        
        // Initial state: if sidebar doesn't have 'hidden-by-click', it's pinned open
        if (!$adminSidebar.hasClass('hidden-by-click')) {
            $adminSidebar.data('pinned-open', true);
        }

        var $backdrop = $('#admin-backdrop');
        function isSmallScreen() { return window.matchMedia('(max-width: 991px)').matches; }
        function closeMobileSidebar() { $adminSidebar.removeClass('mobile-open'); $backdrop.removeClass('show'); }
        $backdrop.on('click', closeMobileSidebar);
        $(window).on('resize', function () { if (!isSmallScreen()) closeMobileSidebar(); });

        $sidebarCollapseButton.on('click', function () {
            if (isSmallScreen()) {
                // On phones the sidebar is a drawer: just open/close it
                var open = !$adminSidebar.hasClass('mobile-open');
                $adminSidebar.toggleClass('mobile-open', open);
                $backdrop.toggleClass('show', open);
                return;
            }
            if ($adminSidebar.data('pinned-open')) {
                // If it's pinned open, click hides it and enables hover
                $adminSidebar.removeClass('hover-active').addClass('hidden-by-click');
                $adminSidebar.data('pinned-open', false);
            } else {
                // If it's hidden or shown by hover, click pins it open
                $adminSidebar.removeClass('hidden-by-click').removeClass('hover-active');
                $adminSidebar.data('pinned-open', true);
            }
        });

        $sidebarHoverZone.on('mouseenter', function () {
            if ($adminSidebar.hasClass('hidden-by-click')) { // Only show on hover if it was explicitly hidden
                showSidebar();
            }
        });

        $adminSidebar.on('mouseleave', function () {
             // If sidebar is not pinned open (i.e., it was hidden by click and then shown by hover)
            if (!$adminSidebar.data('pinned-open')) {
                hideSidebarFromHover();
            }
        });
    }

    // 4. Auto-dismiss flash messages (Bootstrap 4)
    window.setTimeout(function() {
        $(".alert-dismissible.fade.show").each(function() {
            $(this).alert('close');
        });
    }, 7000); // Dismiss after 7 seconds

    // Initialize Bootstrap components like dropdowns, carousels if not auto-init by data attributes
    $('.carousel').carousel();

    // 5. Promo strip: show 3 offers (2 on tablets, 1 on phones) and slide one step every few seconds
    (function () {
        var strip = document.querySelector('.promo-strip');
        if (!strip) return;
        var track = strip.querySelector('.promo-track');
        var tiles = Array.prototype.slice.call(track.children);
        var count = tiles.length;
        var dotsBox = strip.querySelector('.promo-dots');
        var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        var INTERVAL = 4500, index = 0, perView = 3, timer = null, clones = [], paused = false;

        function viewsFor(width) { return width >= 900 ? 3 : (width >= 560 ? 2 : 1); }
        function stepSize() {
            var gap = parseFloat(getComputedStyle(track).columnGap) || 16;
            return tiles[0].getBoundingClientRect().width + gap;
        }
        function moveTo(i, animate) {
            track.style.transition = animate ? 'transform .6s cubic-bezier(.4,0,.2,1)' : 'none';
            track.style.transform = 'translateX(' + (-i * stepSize()) + 'px)';
            index = i;
            updateDots();
        }
        function next() {
            if (index >= count) { moveTo(0, false); void track.offsetWidth; }   // jump from the copied tiles back to the real start
            moveTo(index + 1, true);
        }
        function prev() {
            if (index <= 0) { moveTo(count, false); void track.offsetWidth; }
            moveTo(index - 1, true);
        }
        track.addEventListener('transitionend', function (e) {
            if (e.target !== track) return;          // ignore hover animations inside the tiles
            if (index >= count) moveTo(0, false);   // seamless loop
        });
        function updateDots() {
            if (!dotsBox) return;
            var active = index % count;
            Array.prototype.forEach.call(dotsBox.children, function (d, i) {
                d.classList.toggle('is-active', i === active);
                d.setAttribute('aria-selected', i === active ? 'true' : 'false');
            });
        }
        function buildDots() {
            if (!dotsBox) return;
            dotsBox.innerHTML = '';
            tiles.forEach(function (_, i) {
                var d = document.createElement('button');
                d.type = 'button'; d.className = 'promo-dot'; d.setAttribute('role', 'tab');
                d.setAttribute('aria-label', 'Show offer ' + (i + 1));
                d.addEventListener('click', function () { moveTo(i, true); restart(); });
                dotsBox.appendChild(d);
            });
        }
        function stop() { if (timer) { clearInterval(timer); timer = null; } }
        function start() {
            stop();
            if (reduceMotion || paused || strip.classList.contains('is-static')) return;
            timer = setInterval(next, INTERVAL);
        }
        function restart() { start(); }
        function setup() {
            clones.forEach(function (c) { c.remove(); }); clones = [];
            perView = Math.min(viewsFor(strip.clientWidth), count);
            strip.style.setProperty('--per-view', perView);
            var moving = count > perView;
            strip.classList.toggle('is-static', !moving);
            if (moving) {
                // copy the first tiles to the end so the strip can keep sliding in one direction
                for (var i = 0; i < perView; i++) {
                    var c = tiles[i].cloneNode(true);
                    c.setAttribute('aria-hidden', 'true'); c.setAttribute('tabindex', '-1');
                    track.appendChild(c); clones.push(c);
                }
            }
            moveTo(moving ? Math.min(index, count - 1) : 0, false);
            start();
        }

        var prevBtn = strip.querySelector('.promo-prev'), nextBtn = strip.querySelector('.promo-next');
        if (prevBtn) prevBtn.addEventListener('click', function () { prev(); restart(); });
        if (nextBtn) nextBtn.addEventListener('click', function () { next(); restart(); });
        strip.addEventListener('mouseenter', function () { paused = true; stop(); });
        strip.addEventListener('mouseleave', function () { paused = false; start(); });
        strip.addEventListener('focusin', function () { paused = true; stop(); });
        strip.addEventListener('focusout', function () { paused = false; start(); });
        document.addEventListener('visibilitychange', function () { document.hidden ? stop() : start(); });

        // swipe on phones
        var touchX = null;
        strip.addEventListener('touchstart', function (e) { touchX = e.touches[0].clientX; stop(); }, { passive: true });
        strip.addEventListener('touchend', function (e) {
            if (touchX === null) return;
            var dx = e.changedTouches[0].clientX - touchX; touchX = null;
            if (Math.abs(dx) > 40 && !strip.classList.contains('is-static')) { dx < 0 ? next() : prev(); }
            start();
        });

        var resizeTimer;
        window.addEventListener('resize', function () { clearTimeout(resizeTimer); resizeTimer = setTimeout(setup, 150); });
        buildDots();
        setup();
    })();

});