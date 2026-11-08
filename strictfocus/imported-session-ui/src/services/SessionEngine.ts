/**
 * StrictRecords — Pure Business Logic & Domain Services
 *
 * Completely decoupled from React and UI components. Handles URL policy validation,
 * time formatting, host WebView2 IPC serialization, and telemetry calculations.
 */

import {
  HostCommandMessage,
  IBrowserTab,
  IDeviceConnectionsState,
  IStrictSessionState,
} from '../types/strictSession';

/**
 * Formats seconds into MM:SS or HH:MM:SS tabular string
 */
export function formatSecondsToTimer(totalSeconds: number): string {
  const clamped = Math.max(0, Math.floor(totalSeconds));
  const hours = Math.floor(clamped / 3600);
  const minutes = Math.floor((clamped % 3600) / 60);
  const seconds = clamped % 60;

  const mm = String(minutes).padStart(2, '0');
  const ss = String(seconds).padStart(2, '0');

  if (hours > 0) {
    const hh = String(hours).padStart(2, '0');
    return `${hh}:${mm}:${ss}`;
  }
  return `${mm}:${ss}`;
}

/**
 * Normalizes a raw user input URL string into a standard HTTPS URL and extracts hostname.
 */
export function parseAndNormalizeUrl(rawInput: string): {
  normalizedUrl: string;
  hostname: string;
  isValidUrl: boolean;
} {
  const trimmed = rawInput.trim();
  if (!trimmed) {
    return { normalizedUrl: '', hostname: '', isValidUrl: false };
  }

  let candidate = trimmed;
  if (!/^https?:\/\//i.test(candidate)) {
    candidate = `https://${candidate}`;
  }

  try {
    const parsed = new URL(candidate);
    const hostname = parsed.hostname.toLowerCase().replace(/^www\./, '');
    return {
      normalizedUrl: parsed.toString().replace(/\/$/, ''),
      hostname,
      isValidUrl: Boolean(hostname && hostname.includes('.')),
    };
  } catch {
    return { normalizedUrl: trimmed, hostname: trimmed.toLowerCase(), isValidUrl: false };
  }
}

/**
 * Evaluates whether a target URL is permitted by the active Browser Restriction Policy.
 */
export function evaluateUrlPolicy(
  rawUrl: string,
  allowedDomains: string[]
): {
  allowed: boolean;
  normalizedUrl: string;
  hostname: string;
  faviconType: IBrowserTab['faviconType'];
  title: string;
  blockedReason?: string;
} {
  const { normalizedUrl, hostname, isValidUrl } = parseAndNormalizeUrl(rawUrl);

  if (!isValidUrl) {
    return {
      allowed: false,
      normalizedUrl: rawUrl,
      hostname: rawUrl,
      faviconType: 'BLOCKED',
      title: 'Invalid Address',
      blockedReason: `The entered address "${rawUrl}" is not a valid domain.`,
    };
  }

  const normalizedAllowed = allowedDomains.map((d) => d.toLowerCase().replace(/^www\./, '').trim());

  const isPermitted = normalizedAllowed.some(
    (domain) => hostname === domain || hostname.endsWith(`.${domain}`)
  );

  if (!isPermitted) {
    return {
      allowed: false,
      normalizedUrl,
      hostname,
      faviconType: 'BLOCKED',
      title: `Blocked: ${hostname}`,
      blockedReason: `Domain "${hostname}" is restricted during this Strict Session. Only [${allowedDomains.join(', ')}] are permitted.`,
    };
  }

  if (hostname.includes('chatgpt.com') || hostname.includes('openai.com')) {
    return {
      allowed: true,
      normalizedUrl,
      hostname,
      faviconType: 'CHATGPT',
      title: 'ChatGPT',
    };
  }

  if (hostname.includes('leetcode.com')) {
    return {
      allowed: true,
      normalizedUrl,
      hostname,
      faviconType: 'LEETCODE',
      title: "LeetCode - The World's Leading",
    };
  }

  return {
    allowed: true,
    normalizedUrl,
    hostname,
    faviconType: 'DOCS',
    title: hostname,
  };
}

/**
 * Calculates how many companion devices are currently verified & connected.
 */
export function countConnectedDevices(deviceConnections: IDeviceConnectionsState): {
  connectedCount: number;
  totalSlots: number;
} {
  const slots = Object.values(deviceConnections.devices);
  const connectedCount = slots.filter(
    (d) => d.status === 'HOST' || d.status === 'CONNECTED'
  ).length;
  return {
    connectedCount,
    totalSlots: slots.length,
  };
}

/**
 * Formats a desktop clock string like "10:00 AM, Mon Jan 27, 2025"
 */
export function formatDesktopClock(date: Date = new Date()): string {
  const timePart = date.toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: true,
  });
  const datePart = date.toLocaleDateString('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  });
  return `${timePart}, ${datePart}`;
}

/**
 * Sends a structured command to the Windows EXE WebView2 host if present.
 */
export function sendCommandToHostBridge(command: HostCommandMessage): boolean {
  if (typeof window !== 'undefined' && window.chrome?.webview?.postMessage) {
    try {
      window.chrome.webview.postMessage(command);
      return true;
    } catch {
      return false;
    }
  }
  return false;
}

/**
 * Computes overall session health status for UI badges without hardcoding states.
 */
export function computeSessionHealth(state: IStrictSessionState): {
  label: string;
  sublabel: string;
  isNominal: boolean;
  isAwaitingHost: boolean;
} {
  if (
    state.meta.environmentMode === 'PRODUCTION' &&
    !state.meta.hostBridgeConnected &&
    state.proctoring.providerStatus === 'AWAITING_HOST'
  ) {
    return {
      label: 'Monitoring: Awaiting Host',
      sublabel: 'WebView2 Bridge Standby',
      isNominal: false,
      isAwaitingHost: true,
    };
  }

  if (!state.proctoring.monitoringActive) {
    return {
      label: 'Monitoring: Paused',
      sublabel: 'Session Hold',
      isNominal: false,
      isAwaitingHost: false,
    };
  }

  return {
    label: 'Monitoring: Active',
    sublabel: 'Screen · Camera · Activity',
    isNominal: true,
    isAwaitingHost: false,
  };
}
