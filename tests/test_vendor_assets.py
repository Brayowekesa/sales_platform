"""Front-end libraries are self-hosted under static/vendor: no third-party script/style CDNs."""
import os
import re

from shop.security import CSP

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CDN_HOSTS = ['code.jquery.com', 'cdn.jsdelivr.net', 'stackpath.bootstrapcdn.com', 'cdnjs.cloudflare.com',
             'cdn.datatables.net', 'maxcdn.bootstrapcdn.com', 'unpkg.com']


def _csp_directive(name):
    for part in CSP.split(';'):
        part = part.strip()
        if part.startswith(name + ' '):
            return part
    raise AssertionError(f'{name} missing from CSP')


def test_no_template_references_a_cdn():
    found = []
    for folder, _dirs, files in os.walk(os.path.join(ROOT, 'templates')):
        for name in files:
            path = os.path.join(folder, name)
            with open(path, encoding='utf-8') as f:
                text = f.read()
            found += [f'{os.path.relpath(path, ROOT)}: {host}' for host in CDN_HOSTS if host in text]
    assert not found, found


def test_csp_has_no_cdn_hosts():
    script_src = _csp_directive('script-src')
    assert script_src == "script-src 'self' 'unsafe-inline'"
    assert not re.search(r'https?://', script_src)
    assert _csp_directive('style-src') == "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com"
    assert _csp_directive('font-src') == "font-src 'self' https://fonts.gstatic.com data:"
    for host in CDN_HOSTS:
        assert host not in CSP


def _local_assets(html):
    return re.findall(r'(?:src|href)="(/static/vendor/[^"]+)"', html)


def test_pages_load_libraries_from_static_vendor(app, login):
    client = app.test_client()
    home = client.get('/')
    page = home.data.decode()
    assert "script-src 'self' 'unsafe-inline';" in home.headers['Content-Security-Policy']
    assets = _local_assets(page)
    assert any('jquery-3.7.1/jquery.min.js' in a for a in assets)
    assert any('bootstrap-4.6.2/js/bootstrap.bundle.min.js' in a for a in assets)
    assert any('bootstrap-4.6.2/css/bootstrap.min.css' in a for a in assets)
    assert any('fontawesome-free-5.15.4/css/all.min.css' in a for a in assets)

    adm = login('admin', 'adminpass')
    pages = {
        '/admin/products': 'datatables-1.13.11/js/jquery.dataTables.min.js',
        '/admin/promotions': 'datatables-1.13.11/js/dataTables.bootstrap4.min.js',
        '/admin/reports/sales-by-product': 'datatables-1.13.11/css/dataTables.bootstrap4.min.css',
        '/admin/reports/sales-over-time': 'chartjs-4.6.1/chart.umd.min.js',
    }
    for url, lib in pages.items():
        html = adm.get(url).data.decode()
        assets = _local_assets(html)
        assert any(lib in a for a in assets), url
        assets += _local_assets(page)
        for asset in set(assets):   # every referenced vendor file actually exists and is served
            assert client.get(asset.split('?')[0]).status_code == 200, asset


def test_font_awesome_webfonts_present():
    css = open(os.path.join(ROOT, 'static/vendor/fontawesome-free-5.15.4/css/all.min.css'), encoding='utf-8').read()
    for font in set(re.findall(r'\.\./webfonts/([\w-]+\.woff2)', css)):
        assert os.path.exists(os.path.join(ROOT, 'static/vendor/fontawesome-free-5.15.4/webfonts', font)), font
