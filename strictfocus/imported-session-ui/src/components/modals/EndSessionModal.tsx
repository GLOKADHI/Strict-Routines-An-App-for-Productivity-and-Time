/**
 * StrictRecords — End Session Confirmation & Summary Modal
 *
 * Dispatches `SESSION_REQUEST_END` to the Windows EXE WebView2 Host Bridge
 * and displays the final session telemetry audit report.
 */

import React, { useState } from 'react';
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  RotateCcw,
  ShieldCheck,
  X,
} from 'lucide-react';
import { useStrictSession } from '../../providers/StrictSessionContext';
import { formatSecondsToTimer } from '../../services/SessionEngine';
import {isDesktopHost} from '../../services/DesktopRuntime';
import {DesktopEndModal} from '../integration/DesktopChrome';

interface EndSessionModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export const EndSessionModal: React.FC<EndSessionModalProps> = ({
  isOpen,
  onClose,
}) => {
  if(isDesktopHost)return <DesktopEndModal isOpen={isOpen} onClose={onClose}/>;
  const {
    state,
    isSessionEnded,
    sessionEndSummary,
    endStrictSession,
    restartStrictSession,
  } = useStrictSession();

  const [exitReason, setExitReason] = useState('Completed study goals for session');

  if (!isOpen && !isSessionEnded) {
    return null;
  }

  const handleConfirmEnd = (e: React.FormEvent) => {
    e.preventDefault();
    endStrictSession(exitReason);
  };

  const handleStartNewSession = () => {
    restartStrictSession();
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/75 backdrop-blur-sm flex items-center justify-center p-4">
      <div className="w-full max-w-md bg-[#111722] border border-[#283548] rounded-2xl shadow-2xl overflow-hidden">
        {!isSessionEnded ? (
          <form onSubmit={handleConfirmEnd}>
            <div className="px-5 py-4 border-b border-[#1E293B] flex items-center justify-between">
              <div className="flex items-center gap-2.5">
                <div className="w-8 h-8 rounded-lg bg-red-500/15 border border-red-500/30 flex items-center justify-center text-red-400">
                  <AlertTriangle className="w-4 h-4" />
                </div>
                <div>
                  <h2 className="text-sm font-bold text-white">
                    End Strict Session?
                  </h2>
                  <p className="text-[11px] text-slate-400">
                    Releases companion device locks and notifies Windows Host EXE
                  </p>
                </div>
              </div>
              <button
                type="button"
                onClick={onClose}
                className="text-slate-400 hover:text-white cursor-pointer"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="p-5 space-y-4">
              <div className="grid grid-cols-3 gap-2.5 text-center">
                <div className="bg-[#0B0F17] border border-[#1E293B] rounded-lg p-2.5">
                  <div className="text-[10px] text-slate-400">Remaining</div>
                  <div className="text-sm font-bold text-white font-mono tabular-nums mt-0.5">
                    {formatSecondsToTimer(state.timer.remainingSeconds)}
                  </div>
                </div>
                <div className="bg-[#0B0F17] border border-[#1E293B] rounded-lg p-2.5">
                  <div className="text-[10px] text-slate-400">Violations</div>
                  <div className="text-sm font-bold text-white font-mono tabular-nums mt-0.5">
                    {state.proctoring.violationCount} / {state.proctoring.maxAllowedViolations}
                  </div>
                </div>
                <div className="bg-[#0B0F17] border border-[#1E293B] rounded-lg p-2.5">
                  <div className="text-[10px] text-slate-400">Mode</div>
                  <div className="text-xs font-bold text-emerald-400 mt-1">
                    {state.meta.environmentMode}
                  </div>
                </div>
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-300 mb-1.5">
                  Termination Note (Logged to Host Audit Trail)
                </label>
                <input
                  type="text"
                  value={exitReason}
                  onChange={(e) => setExitReason(e.target.value)}
                  className="w-full bg-[#0B0F17] border border-[#263347] rounded-lg px-3 py-2 text-xs text-slate-100 focus:outline-none focus:border-blue-500"
                />
              </div>
            </div>

            <div className="px-5 py-3.5 bg-[#0D121B] border-t border-[#1E293B] flex items-center justify-end gap-2.5">
              <button
                type="button"
                onClick={onClose}
                className="px-4 py-1.5 rounded-lg bg-[#182130] hover:bg-[#222E42] text-xs font-medium text-slate-200 cursor-pointer"
              >
                Continue Session
              </button>
              <button
                type="submit"
                className="px-4 py-1.5 rounded-lg bg-red-600 hover:bg-red-500 text-xs font-semibold text-white cursor-pointer"
              >
                Confirm End Session
              </button>
            </div>
          </form>
        ) : (
          /* Session Terminated Audit Summary */
          <div>
            <div className="px-5 py-4 border-b border-[#1E293B] flex items-center gap-2.5">
              <div className="w-8 h-8 rounded-lg bg-emerald-500/15 border border-emerald-500/30 flex items-center justify-center text-emerald-400">
                <CheckCircle2 className="w-4 h-4" />
              </div>
              <div>
                <h2 className="text-sm font-bold text-white">
                  Strict Session Completed
                </h2>
                <p className="text-[11px] text-slate-400">
                  SESSION_REQUEST_END dispatched to Host Bridge
                </p>
              </div>
            </div>

            <div className="p-5 space-y-3 text-xs">
              <div className="bg-[#0B0F17] border border-[#1E293B] rounded-lg p-3 space-y-2">
                <div className="flex justify-between">
                  <span className="text-slate-400">Session ID:</span>
                  <span className="font-mono text-slate-200">{state.meta.sessionId}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-400">Elapsed Time:</span>
                  <span className="font-mono tabular-nums text-slate-200 flex items-center gap-1">
                    <Clock className="w-3 h-3 text-blue-400" />
                    {formatSecondsToTimer(sessionEndSummary?.elapsedSeconds ?? 0)}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-400">Recorded Violations:</span>
                  <span className="font-mono tabular-nums text-slate-200 flex items-center gap-1">
                    <ShieldCheck className="w-3 h-3 text-emerald-400" />
                    {sessionEndSummary?.violationsCount ?? 0}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-400">Exit Note:</span>
                  <span className="text-slate-200 truncate max-w-[210px]">
                    {sessionEndSummary?.reason}
                  </span>
                </div>
              </div>
            </div>

            <div className="px-5 py-3.5 bg-[#0D121B] border-t border-[#1E293B] flex items-center justify-end">
              <button
                type="button"
                onClick={handleStartNewSession}
                className="px-4 py-1.5 rounded-lg bg-blue-600 hover:bg-blue-500 text-xs font-semibold text-white flex items-center gap-1.5 cursor-pointer"
              >
                <RotateCcw className="w-3.5 h-3.5" />
                <span>Start New Session</span>
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
};
