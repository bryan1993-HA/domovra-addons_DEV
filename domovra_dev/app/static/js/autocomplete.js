/**
 * Domovra — Autocomplete universel
 * Usage : <input data-autocomplete="stores" placeholder="…">
 *
 * Listes statiques intégrées : stores, units, categories
 * Listes dynamiques (DB)     : locations — injectées via window.AC_LISTS
 *
 * API publique :
 *   window.AC.register(input, options)   — attache sur un input existant
 *   window.AC.refresh()                  — réattache sur tous les [data-autocomplete]
 *   window.AC.LISTS                      — accès aux listes statiques
 */
(function () {
  "use strict";

  /* ── Listes statiques ─────────────────────────────────────────────────── */
  const STATIC_LISTS = {

    stores: [
      "E.Leclerc", "Leclerc Drive",
      "Carrefour", "Carrefour Market", "Carrefour Express",
      "Carrefour City", "Carrefour Contact", "Carrefour Drive",
      "Auchan", "Auchan Supermarché", "Auchan Drive",
      "Hyper U", "Super U", "U Express", "Utile", "Marché U", "U Drive",
      "Intermarché", "Intermarché Express", "Intermarché Contact", "Intermarché Drive",
      "Géant Casino", "Casino Supermarché",
      "Monoprix", "Monop'", "Franprix",
      "Cora", "Bi1", "Match", "Supeco",
      "Lidl", "Aldi", "Netto", "Leader Price",
      "Action", "Normal", "Penny Market",
      "Biocoop", "Naturalia", "La Vie Claire",
      "Bio c'Bon", "Satoriz", "Biomonde", "L'Eau Vive",
      "Grand Frais", "Picard", "Thiriet",
      "Metro", "Costco", "Promocash", "Colruyt",
      "Marché local", "Producteur local", "Chronodrive",
    ],

    units: [
      "pièce", "tranche", "paquet", "boîte", "bocal", "bouteille",
      "sachet", "lot", "barquette", "rouleau", "dosette",
      "g", "kg", "ml", "L",
    ],

    categories: [
      "Fruits & légumes", "Viande", "Poisson & fruits de mer",
      "Produits laitiers", "Oeufs", "Charcuterie", "Fromages",
      "Boulangerie & pâtisserie", "Céréales, pâtes & riz",
      "Épicerie salée", "Épicerie sucrée", "Conserves & bocaux",
      "Condiments & sauces", "Huiles & vinaigres", "Boissons",
      "Surgelés", "Bio / Sans gluten",
      "Hygiène & beauté", "Entretien & nettoyage", "Bébé", "Animaux",
      "Divers",
    ],

  };

  /* ── Résolution de la liste ───────────────────────────────────────────── */
  function resolveList(name) {
    if (Array.isArray(name)) return name;
    // Listes dynamiques injectées par Python/Jinja
    if (window.AC_LISTS && window.AC_LISTS[name]) return window.AC_LISTS[name];
    // Listes statiques
    if (STATIC_LISTS[name]) return STATIC_LISTS[name];
    return [];
  }

  /* ── Normalisation pour la recherche (sans accents, minuscule) ────────── */
  function normalize(str) {
    return String(str || "")
      .toLowerCase()
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "");
  }

  /* ── Filtre des suggestions ───────────────────────────────────────────── */
  function filter(list, query) {
    if (!query) return list.slice(0, 10);
    const q = normalize(query);
    return list
      .filter(item => normalize(item).includes(q))
      .slice(0, 10);
  }

  /* ── Création du dropdown ─────────────────────────────────────────────── */
  function createDropdown() {
    const ul = document.createElement("ul");
    ul.className = "ac-dropdown";
    ul.setAttribute("role", "listbox");
    Object.assign(ul.style, {
      position:        "absolute",
      zIndex:          "9999",
      listStyle:       "none",
      margin:          "4px 0 0",
      padding:         "4px 0",
      background:      "var(--bg-elevated, var(--bg-card, #1a1a2e))",
      border:          "1px solid var(--line, #2d3340)",
      borderRadius:    "10px",
      boxShadow:       "0 8px 24px rgba(0,0,0,.35)",
      minWidth:        "200px",
      maxHeight:       "260px",
      overflowY:       "auto",
      display:         "none",
    });
    return ul;
  }

  /* ── Positionnement du dropdown ───────────────────────────────────────── */
  function positionDropdown(input, ul) {
    const rect = input.getBoundingClientRect();
    const scrollY = window.scrollY || document.documentElement.scrollTop;
    const scrollX = window.scrollX || document.documentElement.scrollLeft;
    ul.style.left  = (rect.left + scrollX) + "px";
    ul.style.top   = (rect.bottom + scrollY) + "px";
    ul.style.width = rect.width + "px";
  }

  /* ── Rendu des items ──────────────────────────────────────────────────── */
  function renderItems(ul, items, query, onSelect) {
    ul.innerHTML = "";
    if (!items.length) {
      ul.style.display = "none";
      return;
    }
    const q = normalize(query);
    items.forEach((item, idx) => {
      const li = document.createElement("li");
      li.setAttribute("role", "option");
      li.dataset.idx = idx;

      // Highlight de la partie correspondante
      if (q) {
        const norm = normalize(item);
        const pos  = norm.indexOf(q);
        if (pos >= 0) {
          li.innerHTML =
            escHtml(item.slice(0, pos)) +
            "<mark>" + escHtml(item.slice(pos, pos + query.length)) + "</mark>" +
            escHtml(item.slice(pos + query.length));
        } else {
          li.textContent = item;
        }
      } else {
        li.textContent = item;
      }

      Object.assign(li.style, {
        padding:    "8px 14px",
        cursor:     "pointer",
        fontSize:   ".875rem",
        lineHeight: "1.4",
        transition: "background .1s",
      });
      li.addEventListener("mouseenter", () => {
        clearActive(ul);
        li.setAttribute("aria-selected", "true");
        li.style.background = "var(--bg-card, rgba(255,255,255,.07))";
      });
      li.addEventListener("mouseleave", () => {
        li.removeAttribute("aria-selected");
        li.style.background = "";
      });
      li.addEventListener("mousedown", e => {
        e.preventDefault(); // évite blur sur l'input
        onSelect(item);
      });
      ul.appendChild(li);
    });

    // Style mark
    ul.querySelectorAll("mark").forEach(m => {
      Object.assign(m.style, {
        background:  "transparent",
        color:       "var(--accent, #818cf8)",
        fontWeight:  "600",
      });
    });

    ul.style.display = "block";
  }

  function clearActive(ul) {
    ul.querySelectorAll("[aria-selected='true']").forEach(li => {
      li.removeAttribute("aria-selected");
      li.style.background = "";
    });
  }

  function escHtml(str) {
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  /* ── Attacher l'autocomplete sur un input ─────────────────────────────── */
  function register(input, options) {
    if (input._acAttached) return;
    input._acAttached = true;

    const listName = options?.list || input.dataset.autocomplete;
    const getList  = () => resolveList(listName);

    // Retirer le datalist natif si présent
    input.removeAttribute("list");

    const ul = createDropdown();
    document.body.appendChild(ul);

    let activeIdx = -1;

    function getItems() {
      return ul.querySelectorAll("li");
    }

    function show() {
      positionDropdown(input, ul);
      const results = filter(getList(), input.value);
      renderItems(ul, results, input.value, select);
      activeIdx = -1;
    }

    function hide() {
      ul.style.display = "none";
      activeIdx = -1;
    }

    function select(value) {
      input.value = value;
      hide();
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      // Marquer comme saisie manuelle (pour session store)
      input.dataset.manuallySet = "1";
    }

    function moveActive(dir) {
      const items = getItems();
      if (!items.length) return;
      clearActive(ul);
      activeIdx = Math.max(0, Math.min(items.length - 1, activeIdx + dir));
      const li = items[activeIdx];
      li.setAttribute("aria-selected", "true");
      li.style.background = "var(--bg-card, rgba(255,255,255,.07))";
      li.scrollIntoView({ block: "nearest" });
    }

    input.addEventListener("focus", show);
    input.addEventListener("input", show);

    input.addEventListener("keydown", e => {
      if (ul.style.display === "none") return;
      if (e.key === "ArrowDown")  { e.preventDefault(); moveActive(1); }
      if (e.key === "ArrowUp")    { e.preventDefault(); moveActive(-1); }
      if (e.key === "Escape")     { hide(); }
      if (e.key === "Enter") {
        const items = getItems();
        if (activeIdx >= 0 && items[activeIdx]) {
          e.preventDefault();
          select(items[activeIdx].textContent.trim());
        }
      }
      if (e.key === "Tab")        { hide(); }
    });

    input.addEventListener("blur", () => {
      // Délai pour laisser le mousedown sur li se déclencher en premier
      setTimeout(hide, 150);
    });

    // Repositionner au scroll/resize
    window.addEventListener("scroll", () => {
      if (ul.style.display !== "none") positionDropdown(input, ul);
    }, { passive: true });
    window.addEventListener("resize", () => {
      if (ul.style.display !== "none") positionDropdown(input, ul);
    }, { passive: true });

    // Clic en dehors
    document.addEventListener("mousedown", e => {
      if (!ul.contains(e.target) && e.target !== input) hide();
    });
  }

  /* ── Auto-attach sur tous les [data-autocomplete] ─────────────────────── */
  function refresh() {
    document.querySelectorAll("[data-autocomplete]").forEach(input => {
      register(input);
    });
  }

  /* ── Init au DOMContentLoaded ─────────────────────────────────────────── */
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", refresh);
  } else {
    refresh();
  }

  /* ── API publique ─────────────────────────────────────────────────────── */
  window.AC = { register, refresh, LISTS: STATIC_LISTS };

})();
