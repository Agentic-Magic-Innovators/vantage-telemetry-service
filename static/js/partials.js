// Telemetry UI — login + dashboard partials only
(function () {
    var PARTIALS = [
        { id: 'partial-login', url: '/static/partials/login.html?v=8' },
        { id: 'dashboard', url: '/static/partials/dashboard.html?v=8' },
    ];
    document.addEventListener('DOMContentLoaded', function () {
        Promise.all(
            PARTIALS.map(function (p) {
                return fetch(p.url)
                    .then(function (r) {
                        if (!r.ok) throw new Error('Failed to load partial: ' + p.url);
                        return r.text();
                    })
                    .then(function (html) {
                        var el = document.getElementById(p.id);
                        if (el) el.innerHTML = html;
                    });
            })
        ).then(function () {
            document.dispatchEvent(new CustomEvent('vantage:ready'));
        }).catch(function (err) {
            console.error('[Partials] Load failed:', err);
            document.dispatchEvent(new CustomEvent('vantage:ready'));
        });
    });
}());
