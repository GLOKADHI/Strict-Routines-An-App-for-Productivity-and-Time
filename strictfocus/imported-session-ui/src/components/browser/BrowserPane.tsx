/**
 * StrictRecords — Modular Restricted Browser Pane Component
 *
 * Implements full browser tab management, per-tab navigation history (Back/Forward/Reload),
 * live URL policy evaluation against `state.browserRestrictions.allowedDomains`,
 * bookmarking, and pluggable content rendering (WebView2 Host surface vs Demo Study Views).
 */

import React, { useEffect, useState } from 'react';
import {
  ArrowLeft,
  ArrowRight,
  ChevronDown,
  ExternalLink,
  Globe,
  Lock,
  MoreVertical,
  Plus,
  RotateCw,
  ShieldAlert,
  Star,
  User,
  X,
} from 'lucide-react';
import { useStrictSession } from '../../providers/StrictSessionContext';
import { evaluateUrlPolicy } from '../../services/SessionEngine';
import { IBrowserTab } from '../../types/strictSession';
import { ChatGptStudyView, OpenAILogoGlyph } from './apps/ChatGptStudyView';
import { LeetCodeLogoGlyph, LeetCodeStudyView } from './apps/LeetCodeStudyView';

interface BrowserPaneProps {
  paneId: 'LEFT' | 'RIGHT';
  initialUrl: string;
}

export const BrowserPane: React.FC<BrowserPaneProps> = ({ paneId, initialUrl }) => {
  const { state, logBrowserNavigation, openMonitoringSection } = useStrictSession();
  const allowedDomains = state.browserRestrictions.allowedDomains;

  const [tabs, setTabs] = useState<IBrowserTab[]>(() => {
    const initialEval = evaluateUrlPolicy(initialUrl, allowedDomains);
    return [
      {
        id: `${paneId.toLowerCase()}-tab-1`,
        title: initialEval.title,
        url: initialEval.normalizedUrl,
        domain: initialEval.hostname,
        faviconType: initialEval.faviconType,
        isBlocked: !initialEval.allowed,
        blockedReason: initialEval.blockedReason,
        history: [initialEval.normalizedUrl],
        historyIndex: 0,
      },
    ];
  });

  const [activeTabId, setActiveTabId] = useState<string>(
    `${paneId.toLowerCase()}-tab-1`
  );
  const [addressInput, setAddressInput] = useState<string>(initialUrl);
  const [bookmarkedUrls, setBookmarkedUrls] = useState<string[]>([
    'https://chatgpt.com',
    'https://leetcode.com',
  ]);
  const [isMenuOpen, setIsMenuOpen] = useState(false);
  const [isTabDropdownOpen, setIsTabDropdownOpen] = useState(false);
  const [reloadCounter, setReloadCounter] = useState(0);

  const activeTab = tabs.find((t) => t.id === activeTabId) || tabs[0];

  // Sync address bar input when switching tabs
  useEffect(() => {
    if (activeTab) {
      setAddressInput(activeTab.url);
    }
  }, [activeTab?.id, activeTab?.url]);

  // Re-evaluate active tabs if allowedDomains policy changes dynamically
  useEffect(() => {
    setTabs((prev) =>
      prev.map((tab) => {
        const res = evaluateUrlPolicy(tab.url, allowedDomains);
        return {
          ...tab,
          title: res.title,
          faviconType: res.faviconType,
          isBlocked: !res.allowed,
          blockedReason: res.blockedReason,
        };
      })
    );
  }, [allowedDomains]);

  const navigateActiveTab = (targetRawUrl: string) => {
    const evaluation = evaluateUrlPolicy(targetRawUrl, allowedDomains);
    logBrowserNavigation(
      paneId,
      evaluation.normalizedUrl,
      evaluation.allowed,
      evaluation.blockedReason
    );

    setTabs((prev) =>
      prev.map((tab) => {
        if (tab.id !== activeTab.id) return tab;
        const nextHistory = [
          ...tab.history.slice(0, tab.historyIndex + 1),
          evaluation.normalizedUrl,
        ];
        return {
          ...tab,
          title: evaluation.title,
          url: evaluation.normalizedUrl,
          domain: evaluation.hostname,
          faviconType: evaluation.faviconType,
          isBlocked: !evaluation.allowed,
          blockedReason: evaluation.blockedReason,
          history: nextHistory,
          historyIndex: nextHistory.length - 1,
        };
      })
    );
    setAddressInput(evaluation.normalizedUrl);
  };

  const handleHistoryBack = () => {
    if (!activeTab || activeTab.historyIndex <= 0) return;
    const nextIndex = activeTab.historyIndex - 1;
    const prevUrl = activeTab.history[nextIndex];
    const evaluation = evaluateUrlPolicy(prevUrl, allowedDomains);

    setTabs((prev) =>
      prev.map((tab) =>
        tab.id === activeTab.id
          ? {
              ...tab,
              title: evaluation.title,
              url: evaluation.normalizedUrl,
              domain: evaluation.hostname,
              faviconType: evaluation.faviconType,
              isBlocked: !evaluation.allowed,
              blockedReason: evaluation.blockedReason,
              historyIndex: nextIndex,
            }
          : tab
      )
    );
  };

  const handleHistoryForward = () => {
    if (!activeTab || activeTab.historyIndex >= activeTab.history.length - 1) return;
    const nextIndex = activeTab.historyIndex + 1;
    const nextUrl = activeTab.history[nextIndex];
    const evaluation = evaluateUrlPolicy(nextUrl, allowedDomains);

    setTabs((prev) =>
      prev.map((tab) =>
        tab.id === activeTab.id
          ? {
              ...tab,
              title: evaluation.title,
              url: evaluation.normalizedUrl,
              domain: evaluation.hostname,
              faviconType: evaluation.faviconType,
              isBlocked: !evaluation.allowed,
              blockedReason: evaluation.blockedReason,
              historyIndex: nextIndex,
            }
          : tab
      )
    );
  };

  const handleAddTab = (defaultUrl?: string) => {
    const fallbackDomain =
      paneId === 'LEFT'
        ? allowedDomains[0] || 'chatgpt.com'
        : allowedDomains[1] || allowedDomains[0] || 'leetcode.com';
    const urlToOpen = defaultUrl || `https://${fallbackDomain}`;
    const evaluation = evaluateUrlPolicy(urlToOpen, allowedDomains);
    const newId = `${paneId.toLowerCase()}-tab-${Date.now()}`;

    const newTab: IBrowserTab = {
      id: newId,
      title: evaluation.title,
      url: evaluation.normalizedUrl,
      domain: evaluation.hostname,
      faviconType: evaluation.faviconType,
      isBlocked: !evaluation.allowed,
      blockedReason: evaluation.blockedReason,
      history: [evaluation.normalizedUrl],
      historyIndex: 0,
    };

    setTabs((prev) => [...prev, newTab]);
    setActiveTabId(newId);
  };

  const handleCloseTab = (tabId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (tabs.length <= 1) return;
    const remaining = tabs.filter((t) => t.id !== tabId);
    setTabs(remaining);
    if (activeTabId === tabId) {
      setActiveTabId(remaining[remaining.length - 1].id);
    }
  };

  const toggleBookmark = () => {
    if (!activeTab) return;
    setBookmarkedUrls((prev) =>
      prev.includes(activeTab.url)
        ? prev.filter((u) => u !== activeTab.url)
        : [...prev, activeTab.url]
    );
  };

  const renderFavicon = (type: IBrowserTab['faviconType']) => {
    switch (type) {
      case 'CHATGPT':
        return <OpenAILogoGlyph className="w-3.5 h-3.5 text-slate-200 shrink-0" />;
      case 'LEETCODE':
        return <LeetCodeLogoGlyph className="w-3.5 h-3.5 shrink-0" />;
      case 'BLOCKED':
        return <ShieldAlert className="w-3.5 h-3.5 text-red-400 shrink-0" />;
      default:
        return <Globe className="w-3.5 h-3.5 text-blue-400 shrink-0" />;
    }
  };

  const isBookmarked = activeTab && bookmarkedUrls.includes(activeTab.url);

  return (
    <div className="h-full w-full flex flex-col bg-[#12161F] border border-[#243044] rounded-xl overflow-hidden shadow-xl">
      {/* 1. Browser Tab Strip */}
      <div className="h-9 bg-[#11151E] px-2 pt-1.5 flex items-center gap-1 border-b border-[#1E2636] shrink-0">
        {/* Tab List Dropdown Button */}
        <div className="relative">
          <button
            type="button"
            onClick={() => setIsTabDropdownOpen((v) => !v)}
            title="Switch Open Tabs"
            className="w-6 h-6 rounded hover:bg-[#1E2636] flex items-center justify-center text-slate-400 hover:text-slate-200 transition-colors cursor-pointer"
          >
            <ChevronDown className="w-3.5 h-3.5" />
          </button>
          {isTabDropdownOpen && (
            <>
              <div
                className="fixed inset-0 z-40"
                onClick={() => setIsTabDropdownOpen(false)}
              />
              <div className="absolute left-0 top-full mt-1 w-56 bg-[#151C28] border border-[#2A374E] rounded-lg shadow-xl py-1 z-50">
                <div className="px-2.5 py-1 text-[10px] font-semibold text-slate-400 border-b border-[#1E2636]">
                  {paneId} Pane Open Tabs ({tabs.length})
                </div>
                {tabs.map((t) => (
                  <button
                    key={t.id}
                    type="button"
                    onClick={() => {
                      setActiveTabId(t.id);
                      setIsTabDropdownOpen(false);
                    }}
                    className="w-full px-2.5 py-1.5 text-left text-xs text-slate-200 hover:bg-[#1E2738] flex items-center gap-2 truncate"
                  >
                    {renderFavicon(t.faviconType)}
                    <span className="truncate">{t.title}</span>
                  </button>
                ))}
              </div>
            </>
          )}
        </div>

        {/* Browser Tabs */}
        <div className="flex items-center gap-1 overflow-x-auto no-scrollbar max-w-[calc(100%-68px)]">
          {tabs.map((tab) => {
            const isTabActive = tab.id === activeTab.id;
            return (
              <div
                key={tab.id}
                onClick={() => setActiveTabId(tab.id)}
                className={`group h-7 min-w-[140px] max-w-[215px] px-3 rounded-t-lg flex items-center justify-between gap-2 text-xs transition-colors cursor-pointer select-none ${
                  isTabActive
                    ? 'bg-[#1B2230] text-slate-100 font-medium border-t border-x border-[#2B374D]'
                    : 'bg-[#141923] text-slate-400 hover:bg-[#181F2C] hover:text-slate-200'
                }`}
              >
                <div className="flex items-center gap-2 min-w-0">
                  {renderFavicon(tab.faviconType)}
                  <span className="truncate text-[12px]">{tab.title}</span>
                </div>
                <button
                  type="button"
                  onClick={(e) => handleCloseTab(tab.id, e)}
                  title={tabs.length > 1 ? 'Close Tab' : 'Primary Session Tab'}
                  className="w-4 h-4 rounded hover:bg-slate-700/60 flex items-center justify-center text-slate-400 hover:text-white shrink-0 cursor-pointer"
                >
                  <X className="w-3 h-3" />
                </button>
              </div>
            );
          })}
        </div>

        {/* New Tab Button */}
        <button
          type="button"
          onClick={() => handleAddTab()}
          title="Open New Restricted Tab"
          className="w-6 h-6 rounded hover:bg-[#1E2636] flex items-center justify-center text-slate-400 hover:text-white transition-colors cursor-pointer shrink-0"
        >
          <Plus className="w-3.5 h-3.5" />
        </button>
      </div>

      {/* 2. Browser Navigation & Address Bar */}
      <div className="h-10 bg-[#1B2230] border-b border-[#263145] px-2.5 flex items-center gap-2 shrink-0">
        {/* Back / Forward / Reload */}
        <div className="flex items-center gap-0.5 shrink-0">
          <button
            type="button"
            onClick={handleHistoryBack}
            disabled={!activeTab || activeTab.historyIndex <= 0}
            title="Back"
            className="w-7 h-7 rounded-md hover:bg-[#252F42] disabled:opacity-35 flex items-center justify-center text-slate-300 transition-colors cursor-pointer disabled:cursor-default"
          >
            <ArrowLeft className="w-3.5 h-3.5" />
          </button>
          <button
            type="button"
            onClick={handleHistoryForward}
            disabled={
              !activeTab || activeTab.historyIndex >= activeTab.history.length - 1
            }
            title="Forward"
            className="w-7 h-7 rounded-md hover:bg-[#252F42] disabled:opacity-35 flex items-center justify-center text-slate-300 transition-colors cursor-pointer disabled:cursor-default"
          >
            <ArrowRight className="w-3.5 h-3.5" />
          </button>
          <button
            type="button"
            onClick={() => setReloadCounter((c) => c + 1)}
            title="Reload Active View"
            className="w-7 h-7 rounded-md hover:bg-[#252F42] flex items-center justify-center text-slate-300 transition-colors cursor-pointer"
          >
            <RotateCw className="w-3.5 h-3.5" />
          </button>
        </div>

        {/* Address Bar Form */}
        <form
          onSubmit={(e) => {
            e.preventDefault();
            navigateActiveTab(addressInput);
          }}
          className="flex-1 flex items-center bg-[#121722] border border-[#2A364B] focus-within:border-blue-500/70 rounded-full h-7 px-3 gap-2 transition-colors"
        >
          {activeTab?.isBlocked ? (
            <ShieldAlert className="w-3.5 h-3.5 text-red-400 shrink-0" />
          ) : (
            <Lock className="w-3 h-3 text-slate-400 shrink-0" />
          )}
          <input
            type="text"
            value={addressInput}
            onChange={(e) => setAddressInput(e.target.value)}
            aria-label={`${paneId} Browser Address Bar`}
            className="flex-1 bg-transparent text-xs text-slate-200 focus:outline-none font-sans truncate select-text"
          />
          <button
            type="button"
            onClick={toggleBookmark}
            title={isBookmarked ? 'Remove Bookmark' : 'Bookmark Page'}
            className="text-slate-400 hover:text-amber-400 transition-colors cursor-pointer shrink-0"
          >
            <Star
              className={`w-3.5 h-3.5 ${
                isBookmarked ? 'text-amber-400 fill-amber-400' : ''
              }`}
            />
          </button>
        </form>

        {/* Profile & Browser Policy Quick-Test Menu */}
        <div className="flex items-center gap-1.5 shrink-0 relative">
          <button
            type="button"
            onClick={() => openMonitoringSection('BROWSER_POLICY')}
            title="StrictRecords Browser Profile Locked"
            className="w-6 h-6 rounded-full bg-[#38527D] text-blue-100 flex items-center justify-center hover:ring-2 hover:ring-blue-400/50 transition-all cursor-pointer"
          >
            <User className="w-3.5 h-3.5" />
          </button>

          <button
            type="button"
            onClick={() => setIsMenuOpen((v) => !v)}
            title="Pane Navigation & Policy Test Menu"
            className="w-6 h-6 rounded hover:bg-[#252F42] flex items-center justify-center text-slate-300 cursor-pointer"
          >
            <MoreVertical className="w-3.5 h-3.5" />
          </button>

          {isMenuOpen && (
            <>
              <div
                className="fixed inset-0 z-40"
                onClick={() => setIsMenuOpen(false)}
              />
              <div className="absolute right-0 top-full mt-1.5 w-60 bg-[#141B27] border border-[#2A374E] rounded-lg shadow-2xl py-1.5 z-50 text-xs">
                <div className="px-3 py-1 text-[10px] font-semibold text-slate-400 border-b border-[#1E283A]">
                  Quick Navigation & Policy Test
                </div>
                <button
                  type="button"
                  onClick={() => {
                    navigateActiveTab('https://chatgpt.com');
                    setIsMenuOpen(false);
                  }}
                  className="w-full px-3 py-1.5 text-left text-slate-200 hover:bg-[#1E283A] flex items-center justify-between"
                >
                  <span>Navigate to chatgpt.com</span>
                  <span className="text-[10px] text-[#22C55E]">Allowed</span>
                </button>
                <button
                  type="button"
                  onClick={() => {
                    navigateActiveTab('https://leetcode.com');
                    setIsMenuOpen(false);
                  }}
                  className="w-full px-3 py-1.5 text-left text-slate-200 hover:bg-[#1E283A] flex items-center justify-between"
                >
                  <span>Navigate to leetcode.com</span>
                  <span className="text-[10px] text-[#22C55E]">Allowed</span>
                </button>
                <div className="my-1 border-t border-[#1E283A]" />
                <button
                  type="button"
                  onClick={() => {
                    navigateActiveTab('https://youtube.com');
                    setIsMenuOpen(false);
                  }}
                  className="w-full px-3 py-1.5 text-left text-red-300 hover:bg-red-950/30 flex items-center justify-between"
                >
                  <span>Test Blocked URL (youtube.com)</span>
                  <span className="text-[10px] font-semibold text-red-400">
                    Blocks + Logs
                  </span>
                </button>
                <button
                  type="button"
                  onClick={() => {
                    navigateActiveTab('https://reddit.com');
                    setIsMenuOpen(false);
                  }}
                  className="w-full px-3 py-1.5 text-left text-red-300 hover:bg-red-950/30 flex items-center justify-between"
                >
                  <span>Test Blocked URL (reddit.com)</span>
                  <span className="text-[10px] font-semibold text-red-400">
                    Blocks + Logs
                  </span>
                </button>
              </div>
            </>
          )}
        </div>
      </div>

      {/* 3. Browser Viewport Content */}
      <div key={reloadCounter} className="flex-1 relative overflow-hidden">
        {activeTab?.isBlocked ? (
          /* Policy Enforcement Blocked Screen */
          <div className="h-full w-full bg-[#0E131C] flex flex-col items-center justify-center p-6 text-center">
            <div className="w-12 h-12 rounded-2xl bg-red-500/15 border border-red-500/40 flex items-center justify-center text-red-400 mb-3">
              <ShieldAlert className="w-6 h-6" />
            </div>
            <h3 className="text-base font-bold text-white">
              Navigation Restricted by Strict Session Policy
            </h3>
            <p className="text-xs text-slate-400 max-w-md mt-1.5 leading-relaxed">
              {activeTab.blockedReason}
            </p>
            <div className="mt-4 p-3 rounded-lg bg-[#141B28] border border-[#243044] text-left max-w-md w-full">
              <div className="text-[10px] font-semibold text-slate-400 mb-1">
                PERMITTED DOMAINS FOR CURRENT SESSION:
              </div>
              <div className="flex flex-wrap gap-1.5">
                {allowedDomains.map((d) => (
                  <button
                    key={d}
                    type="button"
                    onClick={() => navigateActiveTab(`https://${d}`)}
                    className="px-2.5 py-1 rounded bg-[#1C2638] hover:bg-blue-600/30 border border-blue-500/30 text-xs font-mono text-blue-300 flex items-center gap-1 cursor-pointer"
                  >
                    <span>https://{d}</span>
                    <ExternalLink className="w-3 h-3" />
                  </button>
                ))}
              </div>
            </div>
          </div>
        ) : activeTab?.faviconType === 'CHATGPT' ? (
          <ChatGptStudyView />
        ) : activeTab?.faviconType === 'LEETCODE' ? (
          <LeetCodeStudyView />
        ) : (
          /* Custom Whitelisted Domain View / WebView2 Child Host Slot */
          <div className="h-full w-full bg-[#121722] flex flex-col items-center justify-center p-6 text-center">
            <Globe className="w-10 h-10 text-emerald-400 mb-3" />
            <h3 className="text-sm font-bold text-white">
              Whitelisted Resource: {activeTab?.url}
            </h3>
            <p className="text-xs text-slate-400 max-w-md mt-1">
              Domain <strong className="text-slate-200">{activeTab?.domain}</strong> is
              authorized by the active Strict Session policy. When embedded inside the
              Windows EXE WebView2 host, this viewport binds directly to the native child
              browser surface.
            </p>
          </div>
        )}
      </div>
    </div>
  );
};
