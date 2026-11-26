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
                    if (form.closest('.card').parent().parent().parent().prev('h2').text().includes('Wishlist')) {
                         form.closest('.col-lg-4, .col-md-6').fadeOut(500, function() { $(this).remove(); });
                    }
                } else if (typeof response === 'string') {
                    // Not logged in: the server redirected us to the login page
                    showToast('Please log in to continue.', 'info');
                    setTimeout(function() { window.location = '/login'; }, 1200);
                } else {
                    showToast((response && response.message) || 'Could not process item.', 'danger');
                }
            },
            error: function(xhr) {
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
                if (mpesaPhoneNumberInput) mpesaPhoneNumberInput.required = true;
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

});