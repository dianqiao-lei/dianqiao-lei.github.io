/* Approximate visitor cities from our Cloudflare Worker. No demo markers.
   The endpoint stores only city-level aggregates; each page load is one view. */
(function () {
  var map = document.getElementById('visitor-map');
  var status = document.getElementById('visitor-map-status');
  if (!map || !status || !window.fetch) return;
  var endpoint = map.getAttribute('data-endpoint');
  if (!endpoint || location.hostname !== 'dianqiao-lei.github.io') {
    status.textContent = 'Visitor location tracking is disabled in this preview.';
    return;
  }

  var svg = map.querySelector('svg');
  var tip = map.querySelector('.visitor-map__tip');
  var names = window.Intl && Intl.DisplayNames
    ? new Intl.DisplayNames(['en'], { type: 'region' }) : null;

  // Miller projection, matching the existing 900 × 409 dotted world map.
  function project(lat, lon) {
    var R = 6381372, D = Math.PI / 180;
    if (lon < -168.5) lon += 360;
    var x = R * (lon - 11.5) * D;
    var y = -R * Math.log(Math.tan((45 + 0.4 * lat) * D)) / 0.8;
    return [(x + 20004297.151525836) / 40030869.546275226 * 900,
      (y + 12671671.123330014) / 19602063.148465134 * 440.70631074413296];
  }

  function draw(data) {
    if (!data || !Array.isArray(data.places)) throw new Error('Invalid visitor data');
    var places = data.places.filter(function (p) {
      return p && typeof p === 'object' && Number.isFinite(p.lat) && Number.isFinite(p.lon) &&
        Math.abs(p.lat) <= 90 && Math.abs(p.lon) <= 180 &&
        (p.lat !== 0 || p.lon !== 0) &&
        Number.isSafeInteger(p.n) && p.n > 0 &&
        typeof p.city === 'string' && p.city.trim().length > 0 &&
        typeof p.cc === 'string' && /^[A-Z]{2}$/.test(p.cc);
    });
    svg.replaceChildren();
    tip.hidden = true;
    var plotted = 0;
    places.sort(function (a, b) { return b.n - a.n; }).forEach(function (p) {
      var at = project(p.lat, p.lon);
      if (at[0] < 0 || at[0] > 900 || at[1] < 0 || at[1] > 409) return;
      var dot = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      dot.setAttribute('cx', at[0].toFixed(1));
      dot.setAttribute('cy', at[1].toFixed(1));
      dot.setAttribute('r', (3 + Math.min(4, Math.log10(p.n + 1) * 2.4)).toFixed(1));
      var amount = Math.min(1, Math.log(p.n) / Math.log(50));
      dot.style.fill = 'rgb(' + [122, 167, 217].map(function (c, i) {
        return Math.round(c + ([34, 75, 119][i] - c) * amount);
      }).join(',') + ')';
      var country = p.cc;
      try { if (names) country = names.of(p.cc) || p.cc; } catch (e) {}
      var label = p.city + ', ' + country + ' · ' + p.n.toLocaleString('en-US') +
        (p.n === 1 ? ' page view' : ' page views');
      dot.setAttribute('role', 'button');
      dot.setAttribute('tabindex', '0');
      dot.setAttribute('aria-label', label);
      function showTip() {
        tip.textContent = label;
        tip.style.left = at[0] / 9 + '%';
        tip.style.top = at[1] / 4.09 + '%';
        tip.hidden = false;
        // Keep long city labels inside the map on small screens.
        var box = tip.getBoundingClientRect();
        var frame = map.getBoundingClientRect();
        if (box.left < frame.left) tip.style.left = (box.width / 2) + 'px';
        else if (box.right > frame.right) tip.style.left = (frame.width - box.width / 2) + 'px';
      }
      dot.addEventListener('mouseenter', showTip);
      dot.addEventListener('focus', showTip);
      dot.addEventListener('click', showTip);
      dot.addEventListener('keydown', function (event) {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          showTip();
        } else if (event.key === 'Escape') tip.hidden = true;
      });
      dot.addEventListener('mouseleave', function () { tip.hidden = true; });
      dot.addEventListener('blur', function () { tip.hidden = true; });
      svg.appendChild(dot);
      plotted += 1;
    });
    map.setAttribute('aria-label', 'Approximate visitor locations: ' + plotted + ' cities');
    var since = '';
    if (typeof data.startedAt === 'string' && /^\d{4}-\d{2}-\d{2}/.test(data.startedAt)) {
      var started = new Date(data.startedAt.slice(0, 10) + 'T00:00:00Z');
      if (!isNaN(started.getTime())) since = ' · since ' + started.toLocaleDateString('en-US', {
        month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC'
      });
    }
    status.textContent = plotted
      ? 'Approximate visitor locations' + since
      : 'No city-level visits recorded yet.';
  }

  function request(path, method) {
    var controller = new AbortController();
    var timeout = setTimeout(function () { controller.abort(); }, 12000);
    return fetch(endpoint.replace(/\/$/, '') + path, {
      method: method,
      mode: 'cors',
      credentials: 'omit',
      referrerPolicy: 'origin',
      cache: 'no-store',
      signal: controller.signal
    }).then(function (response) {
      if (!response.ok) throw new Error('Visitor statistics unavailable');
      return response.json();
    }).finally(function () { clearTimeout(timeout); });
  }

  status.textContent = 'Loading visitor locations…';
  // Never retry a write: a timed-out request may already have been counted.
  request('/api/visit', 'POST').then(draw).catch(function () {
    return request('/api/visitors', 'GET').then(draw);
  }).catch(function () {
    status.textContent = 'Visitor locations are temporarily unavailable.';
  });
})();
