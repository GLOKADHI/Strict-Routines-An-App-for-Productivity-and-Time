/**
 * StrictRecords — Strict Session Design Tokens
 * Centralized design tokens for colors, surfaces, borders, typography, and layout geometry.
 * Designed for Windows Desktop resolutions (1280x720, 1366x768, 1920x1080, 2560x1440) and WebView2 embedding.
 */

export const STRICT_TOKENS = {
  colors: {
    canvas: '#080B11',
    headerBg: '#0B0F17',
    panelBg: '#0F141E',
    browserChromeBg: '#151B26',
    browserToolbarBg: '#1B2230',
    browserContentBg: '#12161F',
    cardBg: '#111722',
    cardElevated: '#171F2E',
    borderSubtle: '#1E293B',
    borderDefault: '#263347',
    borderStrong: '#334155',
    textPrimary: '#F8FAFC',
    textSecondary: '#94A3B8',
    textMuted: '#64748B',
    status: {
      activeGreen: '#22C55E',
      activeGreenBg: 'rgba(34, 197, 94, 0.12)',
      activeGreenBorder: 'rgba(34, 197, 94, 0.45)',
      warningAmber: '#F59E0B',
      warningAmberBg: 'rgba(245, 158, 11, 0.12)',
      warningAmberBorder: 'rgba(245, 158, 11, 0.45)',
      dangerRed: '#EF4444',
      dangerRedBg: 'rgba(239, 68, 68, 0.12)',
      dangerRedBorder: 'rgba(239, 68, 68, 0.45)',
      infoBlue: '#3B82F6',
      infoBlueBg: 'rgba(59, 130, 246, 0.12)',
      infoBlueBorder: 'rgba(59, 130, 246, 0.45)',
      unverifiedSlate: '#64748B',
    },
  },
  layout: {
    headerHeightPx: 38,
    monitoringStripHeightPx: 62,
    footerHeightPx: 46,
    defaultSplitRatio: 50,
    minPaneWidthPercent: 25,
    maxPaneWidthPercent: 75,
    cameraDefaultWidth: 256,
    cameraDefaultHeight: 158,
    cameraMinWidth: 180,
    cameraMinHeight: 112,
    cameraMaxWidth: 480,
    cameraMaxHeight: 320,
  },
  typography: {
    fontSans: '"Plus Jakarta Sans", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    fontMono: '"JetBrains Mono", "Cascadia Code", "Consolas", monospace',
  },
} as const;

export type SemanticStatus = 'NOMINAL' | 'WARNING' | 'CRITICAL' | 'UNVERIFIED' | 'DEMO';

export function getStatusStyle(status: SemanticStatus) {
  switch (status) {
    case 'NOMINAL':
      return {
        text: 'text-[#22C55E]',
        bg: 'bg-[#22C55E]/10',
        border: 'border-[#22C55E]/40',
        dot: 'bg-[#22C55E]',
      };
    case 'WARNING':
      return {
        text: 'text-amber-400',
        bg: 'bg-amber-500/10',
        border: 'border-amber-500/40',
        dot: 'bg-amber-400',
      };
    case 'CRITICAL':
      return {
        text: 'text-red-400',
        bg: 'bg-red-500/10',
        border: 'border-red-500/40',
        dot: 'bg-red-500',
      };
    case 'DEMO':
      return {
        text: 'text-sky-400',
        bg: 'bg-sky-500/10',
        border: 'border-sky-500/40',
        dot: 'bg-sky-400',
      };
    case 'UNVERIFIED':
    default:
      return {
        text: 'text-slate-400',
        bg: 'bg-slate-800/50',
        border: 'border-slate-700/60',
        dot: 'bg-slate-500',
      };
  }
}
