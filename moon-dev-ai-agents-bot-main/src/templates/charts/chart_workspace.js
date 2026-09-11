/* chart_workspace.js
 * Shared panel/workspace model for the chart pages.
 *
 * Purpose: give each chart page one place that owns panel state, so
 * symbol, timeframe, chart style, indicators, drawings, overlays/markers,
 * data, and render handles are all owned by a panel instance instead of
 * scattered across page-global variables.
 *
 * This pass does NOT merge the two pages into one UI and does NOT add
 * favorites, AI chat features, footprint settings, or multi-panel chrome.
 * It only introduces the model and makes both pages panel-owned.
 */
(function () {
  'use strict';

  var PANEL_TYPES = ['footprint'];

  function createPanel(options) {
    options = options || {};
    var type = options.type || 'footprint';
    if (PANEL_TYPES.indexOf(type) === -1) {
      throw new Error('Unknown chart panel type: ' + type);
    }
    return {
      id: options.id || ('panel-' + Math.random().toString(36).slice(2, 10)),
      type: type,

      // owned chart config
      symbol: options.symbol || '',
      timeframe: options.timeframe || '',
      chartStyle: options.chartStyle || '',

      // owned state sets: one owner per concern
      indicators: options.indicators || {},
      drawings: options.drawings || [],
      overlays: options.overlays || [],
      markers: options.markers || [],

      // panel-specific data payload
      data: options.data || null,

      // panel-local render handles (set by the page host)
      ui: options.ui || null,

      // host hooks so the workspace can dispatch without knowing the render
      // internals of the footprint overlay.
      hooks: options.hooks || null,

      // panel-local cache for page-specific state that does not belong to the
      // shared ownership model yet (for example a websocket handle).
      private: options.private || {}
    };
  }

  function createWorkspace() {
    var panels = [];
    var activePanelId = null;

    function addPanel(panel) {
      panels.push(panel);
      if (!activePanelId) activePanelId = panel.id;
      return panel;
    }

    function removePanel(panelId) {
      var idx = -1;
      for (var i = 0; i < panels.length; i++) {
        if (panels[i].id === panelId) { idx = i; break; }
      }
      if (idx === -1) return null;
      var removed = panels.splice(idx, 1)[0];
      if (activePanelId === panelId) {
        activePanelId = panels.length ? panels[0].id : null;
      }
      return removed;
    }

    function getPanel(panelId) {
      for (var i = 0; i < panels.length; i++) {
        if (panels[i].id === panelId) return panels[i];
      }
      return null;
    }

    function activePanel() {
      return activePanelId ? getPanel(activePanelId) : null;
    }

    function setActivePanel(panelId) {
      if (!getPanel(panelId)) return false;
      activePanelId = panelId;
      return true;
    }

    function allPanels() {
      return panels.slice();
    }

    return {
      panels: panels,
      activePanelId: activePanelId,
      addPanel: addPanel,
      removePanel: removePanel,
      getPanel: getPanel,
      activePanel: activePanel,
      setActivePanel: setActivePanel,
      allPanels: allPanels
    };
  }

  function dispatchPanel(panel, action, payload) {
    if (!panel.hooks || typeof panel.hooks[action] !== 'function') return;
    return panel.hooks[action](panel, payload);
  }

  window.ChartWorkspace = {
    PANEL_TYPES: PANEL_TYPES,
    createPanel: createPanel,
    createWorkspace: createWorkspace,
    dispatchPanel: dispatchPanel
  };
})();
