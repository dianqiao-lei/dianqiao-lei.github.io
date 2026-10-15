/* Genuine city aggregates: daily mode reads a saved same-origin snapshot only.
   Legacy live mode records production page views without retrying writes. */
(function () {
  var map = document.getElementById('visitor-map');
  var status = document.getElementById('visitor-map-status');
  if (!map || !status) return;
  var svg = map.querySelector('svg');
  var tip = map.querySelector('.visitor-map__tip');
  if (!svg || !tip) return;
  var endpoint = map.getAttribute('data-endpoint');
  var production = location.hostname === 'dianqiao-lei.github.io';
  var daily = map.getAttribute('data-refresh') === 'daily';
  // Keep the new daily source separate from cached legacy collector totals.
  var cacheKey = daily ? 'visitor-map-daily-v1' : 'visitor-map-public-v1';
  var current = null;
  var pending = 0;

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

  function timestamp(value) {
    if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(value)) {
      throw new Error('Invalid statistics date');
    }
    var date = new Date(value);
    if (!Number.isFinite(date.getTime()) || date.toISOString() !== value ||
        date.getTime() > Date.now() + 300000) throw new Error('Invalid statistics date');
    return value;
  }

  // Reject the whole response if any city or total is malformed. Copy only the
  // public aggregate fields, so neither extra response fields nor IPs enter cache.
  function validate(data, saved) {
    if (!data || typeof data !== 'object' || Array.isArray(data) ||
        !Array.isArray(data.places) || data.places.length > 20000) throw new Error('Invalid visitor data');
    var sum = 0;
    var places = data.places.map(function (p) {
      if (!p || typeof p !== 'object' || Array.isArray(p) ||
          !Number.isFinite(p.lat) || !Number.isFinite(p.lon) ||
          Math.abs(p.lat) > 90 || Math.abs(p.lon) > 180 || (p.lat === 0 && p.lon === 0) ||
          !Number.isSafeInteger(p.n) || p.n <= 0 ||
          typeof p.city !== 'string' || !p.city.trim() || p.city.length > 120 ||
          /[\u0000-\u001f\u007f]/.test(p.city) ||
          typeof p.cc !== 'string' || !/^[A-Z]{2}$/.test(p.cc) || p.cc === 'XX') {
        throw new Error('Invalid city record');
      }
      sum += p.n;
      if (!Number.isSafeInteger(sum)) throw new Error('Invalid visitor total');
      return { lat: p.lat, lon: p.lon, cc: p.cc, city: p.city.trim(), n: p.n };
    });
    if (!Number.isSafeInteger(data.mappedPageviews) || data.mappedPageviews < 0 ||
        !Number.isSafeInteger(data.unmappedPageviews) || data.unmappedPageviews < 0 ||
        data.mappedPageviews !== sum ||
        !Number.isSafeInteger(sum + data.unmappedPageviews)) throw new Error('Invalid visitor totals');
    var startedAt = data.startedAt === null && sum + data.unmappedPageviews === 0
      ? null : timestamp(data.startedAt);
    var updatedAt = saved || Object.prototype.hasOwnProperty.call(data, 'updatedAt')
      ? timestamp(data.updatedAt) : new Date().toISOString();
    if (startedAt && startedAt > updatedAt) throw new Error('Invalid statistics date order');
    return { places: places, mappedPageviews: sum, unmappedPageviews: data.unmappedPageviews,
      startedAt: startedAt, updatedAt: updatedAt };
  }

  function displayDate(value, includeTime) {
    var options = { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' };
    if (includeTime) {
      options.hour = '2-digit';
      options.minute = '2-digit';
      options.hour12 = false;
    }
    return new Date(value).toLocaleString('en-US', options) + (includeTime ? ' UTC' : '');
  }

  function draw(data, saved) {
    var places = data.places.slice();
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
        (p.n === 1 ? ' visit' : ' visits');
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
    map.setAttribute('aria-label', 'Approximate visitor locations: ' + plotted +
      (plotted === 1 ? ' city' : ' cities'));
    var label = plotted ? 'Approximate visitor locations' : 'No city-level visits recorded yet';
    status.textContent = label + (daily ? ' · updated daily' : '') + (saved
      ? ' · saved ' + displayDate(data.updatedAt, true)
      : (data.startedAt ? ' · since ' + displayDate(data.startedAt, false) : '.'));
  }

  function accept(input, saved) {
    var data = validate(input, saved);
    if (current) {
      // The collector may finish before a slow snapshot fetch. Once live data is
      // displayed, never replace it with a saved copy; never roll counters back.
      if ((!current.saved && saved) || data.mappedPageviews < current.data.mappedPageviews ||
          data.unmappedPageviews < current.data.unmappedPageviews ||
          (saved && data.updatedAt < current.data.updatedAt)) return;
    }
    draw(data, saved);
    current = { data: data, saved: saved };
    try { window.localStorage.setItem(cacheKey, JSON.stringify(data)); } catch (e) {}
  }

  function request(url, method, sameOrigin) {
    var controller = window.AbortController ? new AbortController() : null;
    var timeout;
    // Explicit rejection also bounds older browsers without AbortController.
    var expired = new Promise(function (_, reject) {
      timeout = setTimeout(function () {
        if (controller) controller.abort();
        reject(new Error('Visitor statistics timed out'));
      }, 12000);
    });
    var options = { method: method, mode: sameOrigin ? 'same-origin' : 'cors',
      credentials: 'omit', referrerPolicy: 'origin', cache: 'no-store' };
    if (controller) options.signal = controller.signal;
    var fetched = Promise.resolve().then(function () { return fetch(url, options); }).then(function (response) {
      if (!response.ok) throw new Error('Visitor statistics unavailable');
      return response.json();
    });
    return Promise.race([fetched, expired]).finally(function () { clearTimeout(timeout); });
  }

  function finished() {
    pending -= 1;
    if (!pending && !current) {
      status.textContent = production
        ? 'Visitor locations are temporarily unavailable.'
        : 'No saved visitor locations are available in this preview.';
    }
  }

  status.textContent = 'Loading visitor locations…';
  try {
    var cached = window.localStorage.getItem(cacheKey);
    if (cached) accept(JSON.parse(cached), true);
  } catch (e) {}
  if (!window.fetch) {
    if (!current) status.textContent = 'Visitor locations are unavailable in this browser.';
    return;
  }

  // This file travels with the homepage, so saved cities remain available even
  // when the visitor's network cannot reach the live statistics service.
  var snapshot = map.getAttribute('data-snapshot');
  try {
    var snapshotURL = snapshot ? new URL(snapshot, location.href) : null;
    if (snapshotURL && snapshotURL.origin === location.origin &&
        /^https?:$/.test(snapshotURL.protocol) && !snapshotURL.username && !snapshotURL.password) {
      pending += 1;
      request(snapshotURL.href, 'GET', true).then(function (data) { accept(data, true); })
        .catch(function () {}).finally(finished);
    }
  } catch (e) {}

  if (endpoint && production && !daily) {
    pending += 1;
    var base = endpoint.replace(/\/$/, '');
    // Never retry a write: a failed or timed-out POST may already have counted.
    request(base + '/api/visit', 'POST', false).then(function (data) { accept(data, false); })
      .catch(function () {
        return request(base + '/api/visitors', 'GET', false)
          .then(function (data) { accept(data, false); });
      }).catch(function () {}).finally(finished);
  }
  if (!pending && !current) {
    status.textContent = production ? 'Visitor locations are temporarily unavailable.'
      : 'Visitor location tracking is disabled in this preview.';
  }

})();
