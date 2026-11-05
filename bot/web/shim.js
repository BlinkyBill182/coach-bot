/* Gives tracker.html the claude.use("db") / claude.use("user") interface it uses on
   claude.ai, backed by the bot's REST store. Live updates by polling every 4 seconds. */
(function () {
  var tg = window.Telegram && window.Telegram.WebApp;
  if (tg) {
    try {
      tg.ready();
      tg.expand();
      document.documentElement.setAttribute("data-theme", tg.colorScheme === "dark" ? "dark" : "light");
      tg.onEvent("themeChanged", function () {
        document.documentElement.setAttribute("data-theme", tg.colorScheme === "dark" ? "dark" : "light");
      });
    } catch (e) {}
  }
  var key = new URLSearchParams(location.search).get("key") || "";
  var POLL = 4000;
  var listeners = [];

  function headers(json) {
    var h = {};
    if (tg && tg.initData) h["X-Telegram-Init-Data"] = tg.initData;
    if (key) h["X-Access-Key"] = key;
    if (json) h["Content-Type"] = "application/json";
    return h;
  }
  function call(method, url, body) {
    return fetch(url, { method: method, headers: headers(body !== undefined), body: body === undefined ? undefined : JSON.stringify(body) })
      .then(function (r) {
        return r.json().catch(function () { return {}; }).then(function (j) {
          if (!r.ok) throw { code: j.code || (r.status === 403 ? "not_granted" : "unavailable"), message: j.message || ("HTTP " + r.status) };
          return j;
        });
      }, function () { throw { code: "unavailable", message: "network error" }; });
  }
  function enc(path) { return path.split("/").map(encodeURIComponent).join("/"); }
  var META = { fromCache: false, hasPendingWrites: false };
  function docSnap(id, exists, data) {
    var frozen = exists ? JSON.parse(JSON.stringify(data)) : undefined;
    return { id: id, exists: exists, data: function () { return frozen; }, metadata: META };
  }
  function refreshAll() { listeners.forEach(function (l) { l.poll(true); }); }
  function write(method, path, body) {
    return call(method, "/api/d/" + enc(path), body).then(function () { refreshAll(); });
  }

  function listen(fetcher, next, error) {
    var last = null, dead = false, timer = null;
    var l = {
      poll: function (force) {
        if (dead) return;
        fetcher().then(function (res) {
          if (dead) return;
          if (force || res.sig !== last) { last = res.sig; next(res.snap); }
        }, function (e) {
          if (e && e.code === "not_granted") { dead = true; clearInterval(timer); if (error) error(e); }
        });
      }
    };
    listeners.push(l);
    l.poll(true);
    timer = setInterval(function () { if (!document.hidden) l.poll(false); }, POLL);
    return function () { dead = true; clearInterval(timer); listeners = listeners.filter(function (x) { return x !== l; }); };
  }

  function docRef(path) {
    var id = path.split("/").pop();
    var ref = {
      id: id, path: path,
      get: function () {
        return call("GET", "/api/d/" + enc(path)).then(function (j) { return docSnap(id, j.exists, j.data); });
      },
      set: function (data) { return write("PUT", path, data); },
      update: function (data) { return write("PATCH", path, data); },
      delete: function () { return write("DELETE", path); },
      onSnapshot: function (next, error) {
        return listen(function () {
          return call("GET", "/api/d/" + enc(path)).then(function (j) {
            return { sig: j.version + ":" + j.exists, snap: docSnap(id, j.exists, j.data) };
          });
        }, next, error);
      },
      collection: function (sub) { return collRef(path + "/" + sub); }
    };
    return ref;
  }

  function collRef(path) {
    function fetchAll() {
      return call("GET", "/api/c/" + enc(path)).then(function (j) {
        var docs = j.docs.map(function (d) { return docSnap(d.id, true, d.data); });
        return {
          sig: String(j.seq),
          snap: { docs: docs, size: docs.length, empty: !docs.length, metadata: META,
                  docChanges: function () { return docs.map(function (d, i) { return { type: "added", doc: d, oldIndex: -1, newIndex: i }; }); } }
        };
      });
    }
    return {
      path: path,
      doc: function (id) { return docRef(path + "/" + (id || Math.random().toString(36).slice(2, 12))); },
      add: function (data) { var r = this.doc(); return r.set(data).then(function () { return r; }); },
      get: function () { return fetchAll().then(function (r) { return r.snap; }); },
      onSnapshot: function (next, error) { return listen(fetchAll, next, error); },
      where: function () { return this; }, orderBy: function () { return this; }, limit: function () { return this; }
    };
  }

  var db = { doc: docRef, collection: collRef };
  var user = {
    id: function () { return Promise.resolve(null); },
    isOwner: function () { return Promise.resolve(true); },
    canEdit: function () { return Promise.resolve(true); },
    can: function () { return Promise.resolve(true); },
    me: function () { return Promise.resolve({ id: null, name: "", email: null, isOwner: true, canEdit: true, avatarUrl: "", color: "#0B6E8A" }); },
    profiles: function (ids) { var o = {}; [].concat(ids).forEach(function (i) { o[i] = { id: i, name: "", avatarUrl: "", color: "#0B6E8A", email: null, isMe: false, guest: false }; }); return Promise.resolve(o); }
  };
  window.claude = {
    use: function (name) {
      return Promise.resolve(name === "db" ? db : name === "user" ? user : null);
    }
  };
})();
