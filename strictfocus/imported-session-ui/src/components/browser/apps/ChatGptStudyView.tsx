/**
 * StrictRecords — Interactive ChatGPT Study Workspace View
 *
 * Matches the left pane in the reference screenshot and makes every sidebar thread,
 * prompt input, and quick action pill 100% interactive for testing in AI Studio.
 */

import React, { useState } from 'react';
import {
  ArrowUp,
  BookOpen,
  Globe,
  Lightbulb,
  MoreHorizontal,
  PenSquare,
  Plus,
  Search,
  Sparkles,
} from 'lucide-react';

interface ChatMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  codeSnippet?: string;
}

interface ChatThread {
  id: string;
  title: string;
  group: 'TODAY' | 'YESTERDAY';
  messages: ChatMessage[];
}

const INITIAL_THREADS: ChatThread[] = [
  {
    id: 'dp-lis',
    title: 'DP – Longest Increasing Sub...',
    group: 'TODAY',
    messages: [
      {
        id: 'm1',
        role: 'user',
        content: 'How do we optimize Longest Increasing Subsequence from O(n²) to O(n log n)?',
      },
      {
        id: 'm2',
        role: 'assistant',
        content:
          'We maintain a monotonic `tails` array where `tails[i]` stores the smallest tail of all increasing subsequences of length `i + 1`. For each number, use binary search (`lower_bound`) to update `tails` in O(log n) time.',
        codeSnippet: `function lengthOfLIS(nums: number[]): number {\n  const tails: number[] = [];\n  for (const x of nums) {\n    let l = 0, r = tails.length;\n    while (l < r) {\n      const mid = (l + r) >> 1;\n      if (tails[mid] < x) l = mid + 1;\n      else r = mid;\n    }\n    tails[l] = x;\n  }\n  return tails.length;\n}`,
      },
    ],
  },
  {
    id: 'sliding-window',
    title: 'Sliding Window Problems',
    group: 'TODAY',
    messages: [
      {
        id: 'sw1',
        role: 'user',
        content: 'Give me the universal template for variable-size sliding window problems.',
      },
      {
        id: 'sw2',
        role: 'assistant',
        content:
          'Expand the `right` pointer each step to include elements in your window state. Use a `while` loop to shrink `left` whenever the window constraint is violated (e.g. duplicate characters in Longest Substring Without Repeating Characters).',
        codeSnippet: `let left = 0, best = 0;\nconst freq = new Map<string, number>();\nfor (let right = 0; right < s.length; right++) {\n  freq.set(s[right], (freq.get(s[right]) ?? 0) + 1);\n  while ((freq.get(s[right]) ?? 0) > 1) {\n    freq.set(s[left], (freq.get(s[left]) ?? 1) - 1);\n    left++;\n  }\n  best = Math.max(best, right - left + 1);\n}`,
      },
    ],
  },
  {
    id: 'graph-basics',
    title: 'Graph Basics Revision',
    group: 'TODAY',
    messages: [
      {
        id: 'gb1',
        role: 'user',
        content: 'When should I pick Dijkstra vs 0-1 BFS vs Topological Sort for shortest paths?',
      },
      {
        id: 'gb2',
        role: 'assistant',
        content:
          '• Unweighted graph: Standard BFS — O(V + E)\n• Edge weights in {0, 1}: 0-1 BFS with Deque — O(V + E)\n• Non-negative weights: Dijkstra with Min-Heap — O((V + E) log V)\n• Directed Acyclic Graph (DAG): Topological Sort relaxation — O(V + E)',
      },
    ],
  },
  {
    id: 'binary-search',
    title: 'Binary Search Variations',
    group: 'YESTERDAY',
    messages: [
      {
        id: 'bs1',
        role: 'user',
        content: 'Explain how Median of Two Sorted Arrays achieves O(log(min(m, n))).',
      },
      {
        id: 'bs2',
        role: 'assistant',
        content:
          'Instead of merging the arrays, we binary search the partition cut `i` on the smaller array `A`, which automatically determines cut `j = half - i` on array `B`. Valid partition requires `ALeft <= BRight` and `BLeft <= ARight`.',
      },
    ],
  },
  {
    id: 'study-plan',
    title: 'Study Plan for DSA',
    group: 'YESTERDAY',
    messages: [
      {
        id: 'sp1',
        role: 'user',
        content: 'What order should I revise topics for technical interviews?',
      },
      {
        id: 'sp2',
        role: 'assistant',
        content:
          '1. Arrays, Hashing & Two Pointers\n2. Sliding Window & Binary Search\n3. Linked Lists & Trees (DFS/BFS)\n4. Heaps, Backtracking & Graphs\n5. 1D/2D Dynamic Programming',
      },
    ],
  },
];

export const OpenAILogoGlyph: React.FC<{ className?: string }> = ({ className = 'w-5 h-5' }) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" className={className}>
    <path d="M12 3a4.5 4.5 0 0 0-3.89 2.25L4.22 12a4.5 4.5 0 0 0 0 4.5l2.1 3.64a4.5 4.5 0 0 0 6.15 1.65L19.25 18a4.5 4.5 0 0 0 1.65-6.15l-2.1-3.64A4.5 4.5 0 0 0 12 3Z" />
    <circle cx="12" cy="12" r="3" />
    <path d="M12 9V3M9.4 10.5 4.2 7.5M9.4 13.5l-5.2 3M12 15v6M14.6 13.5l5.2 3M14.6 10.5l5.2-3" />
  </svg>
);

export const ChatGptStudyView: React.FC = () => {
  const [threads, setThreads] = useState<ChatThread[]>(INITIAL_THREADS);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [promptText, setPromptText] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  const [isSearchingSidebar, setIsSearchingSidebar] = useState(false);
  const [webSearchActive, setWebSearchActive] = useState(false);
  const [reasonModeActive, setReasonModeActive] = useState(false);

  const activeThread = threads.find((t) => t.id === activeThreadId) || null;

  const filteredThreads = threads.filter((t) =>
    t.title.toLowerCase().includes(searchQuery.toLowerCase())
  );

  const handleSendMessage = (customPrompt?: string) => {
    const text = (customPrompt ?? promptText).trim();
    if (!text) return;

    const userMsg: ChatMessage = {
      id: `u-${Date.now()}`,
      role: 'user',
      content: text,
    };

    let replyContent =
      'Here is a structured breakdown tailored for your DSA practice session. Focus on invariants, time complexity bounds, and edge cases (empty input, single element, duplicate values).';
    let replyCode: string | undefined;

    if (text.toLowerCase().includes('two sum')) {
      replyContent =
        'For Two Sum (#1), use a single-pass Hash Map storing `complement -> index`. This reduces time complexity from O(n²) brute force to O(n) time and O(n) space:';
      replyCode = `function twoSum(nums: number[], target: number): number[] {\n  const seen = new Map<number, number>();\n  for (let i = 0; i < nums.length; i++) {\n    const need = target - nums[i];\n    if (seen.has(need)) return [seen.get(need)!, i];\n    seen.set(nums[i], i);\n  }\n  return [];\n}`;
    } else if (text.toLowerCase().includes('explain a dsa concept')) {
      replyContent =
        'Monotonic Stack is a stack whose elements maintain a strictly increasing or decreasing order. It solves "Next Greater Element" and "Largest Rectangle in Histogram" in O(n) amortized time because each element is pushed and popped at most once.';
      replyCode = `// Next Greater Element Template (O(n))\nconst stack: number[] = [];\nfor (let i = 0; i < nums.length; i++) {\n  while (stack.length && nums[stack[stack.length - 1]] < nums[i]) {\n    const idx = stack.pop()!;\n    result[idx] = nums[i];\n  }\n  stack.push(i);\n}`;
    } else if (text.toLowerCase().includes('help me solve')) {
      replyContent =
        'Let’s break down the problem systematically:\n1. Identify constraints (e.g., N ≤ 10⁵ implies O(N log N) or O(N)).\n2. Formulate the brute-force recurrence first.\n3. Look for overlapping subproblems or monotonic properties.';
    } else if (text.toLowerCase().includes('review my code')) {
      replyContent =
        'Checklist for reviewing your LeetCode solution before submitting:\n• Integer overflow on `left + right` (use `left + ((right - left) >> 1)`)\n• Off-by-one boundary conditions on sliding windows\n• Null/empty array or single-node linked list checks';
    }

    const assistantMsg: ChatMessage = {
      id: `a-${Date.now() + 1}`,
      role: 'assistant',
      content: replyContent,
      codeSnippet: replyCode,
    };

    if (!activeThreadId) {
      const newThread: ChatThread = {
        id: `t-${Date.now()}`,
        title: text.slice(0, 28) + (text.length > 28 ? '...' : ''),
        group: 'TODAY',
        messages: [userMsg, assistantMsg],
      };
      setThreads((prev) => [newThread, ...prev]);
      setActiveThreadId(newThread.id);
    } else {
      setThreads((prev) =>
        prev.map((t) =>
          t.id === activeThreadId
            ? { ...t, messages: [...t.messages, userMsg, assistantMsg] }
            : t
        )
      );
    }

    setPromptText('');
  };

  return (
    <div className="h-full w-full flex bg-[#161B26] text-slate-100 overflow-hidden select-text">
      {/* Left Sidebar */}
      <aside className="w-[215px] bg-[#10141D] border-r border-[#1E2636] flex flex-col justify-between p-2.5 shrink-0">
        <div className="space-y-1 overflow-y-auto pr-0.5">
          {/* Sidebar Header */}
          <div className="flex items-center justify-between px-2 py-1.5 mb-1">
            <div className="flex items-center gap-2">
              <OpenAILogoGlyph className="w-5 h-5 text-white" />
              <span className="text-[14px] font-bold text-white tracking-tight">
                ChatGPT
              </span>
            </div>
            <button
              type="button"
              onClick={() => setActiveThreadId(null)}
              title="Start New Chat"
              className="p-1 rounded hover:bg-[#1E2534] text-slate-300 hover:text-white transition-colors cursor-pointer"
            >
              <PenSquare className="w-4 h-4" />
            </button>
          </div>

          {/* Primary Nav Buttons */}
          <button
            type="button"
            onClick={() => setActiveThreadId(null)}
            className={`w-full flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-xs font-medium transition-colors cursor-pointer ${
              activeThreadId === null
                ? 'bg-[#202736] text-white'
                : 'text-slate-300 hover:bg-[#19202D]'
            }`}
          >
            <PenSquare className="w-3.5 h-3.5 text-slate-300" />
            <span>New chat</span>
          </button>

          <button
            type="button"
            onClick={() => setIsSearchingSidebar((v) => !v)}
            className="w-full flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-xs font-medium text-slate-300 hover:bg-[#19202D] transition-colors cursor-pointer"
          >
            <Search className="w-3.5 h-3.5 text-slate-400" />
            <span>Search chats</span>
          </button>

          {isSearchingSidebar && (
            <div className="px-1 py-1">
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Filter threads..."
                className="w-full bg-[#161C28] border border-[#283347] rounded px-2 py-1 text-[11px] text-slate-200 placeholder:text-slate-500 focus:outline-none focus:border-slate-500"
              />
            </div>
          )}

          <button
            type="button"
            onClick={() => handleSendMessage('Give me a cheat sheet of common DSA time complexities')}
            className="w-full flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-xs font-medium text-slate-300 hover:bg-[#19202D] transition-colors cursor-pointer"
          >
            <BookOpen className="w-3.5 h-3.5 text-slate-400" />
            <span>Library</span>
          </button>

          {/* Today Group */}
          <div className="pt-3">
            <div className="px-2.5 pb-1 text-[10px] font-medium text-slate-400">
              Today
            </div>
            <div className="space-y-0.5">
              {filteredThreads
                .filter((t) => t.group === 'TODAY')
                .map((thread) => (
                  <button
                    key={thread.id}
                    type="button"
                    onClick={() => setActiveThreadId(thread.id)}
                    className={`w-full text-left px-2.5 py-1.5 rounded-lg text-[12px] truncate transition-colors cursor-pointer ${
                      activeThreadId === thread.id
                        ? 'bg-[#202736] text-white font-medium'
                        : 'text-slate-300 hover:bg-[#181F2C]'
                    }`}
                  >
                    {thread.title}
                  </button>
                ))}
            </div>
          </div>

          {/* Yesterday Group */}
          <div className="pt-3">
            <div className="px-2.5 pb-1 text-[10px] font-medium text-slate-400">
              Yesterday
            </div>
            <div className="space-y-0.5">
              {filteredThreads
                .filter((t) => t.group === 'YESTERDAY')
                .map((thread) => (
                  <button
                    key={thread.id}
                    type="button"
                    onClick={() => setActiveThreadId(thread.id)}
                    className={`w-full text-left px-2.5 py-1.5 rounded-lg text-[12px] truncate transition-colors cursor-pointer ${
                      activeThreadId === thread.id
                        ? 'bg-[#202736] text-white font-medium'
                        : 'text-slate-300 hover:bg-[#181F2C]'
                    }`}
                  >
                    {thread.title}
                  </button>
                ))}
            </div>
          </div>
        </div>

        {/* Bottom Account Footer */}
        <div className="pt-2 border-t border-[#1E2636] flex items-center gap-2.5 px-2 py-1.5 rounded-lg hover:bg-[#181F2C] transition-colors cursor-pointer">
          <div className="w-6 h-6 rounded-full bg-[#3B4C6B] text-white text-xs font-semibold flex items-center justify-center shrink-0">
            S
          </div>
          <span className="text-xs font-medium text-slate-200 truncate">
            My Account
          </span>
        </div>
      </aside>

      {/* Main Chat Viewport */}
      <main className="flex-1 flex flex-col justify-between p-5 overflow-y-auto">
        {activeThread === null ? (
          /* Default Empty / New Chat State matching screenshot */
          <div className="my-auto max-w-[520px] w-full mx-auto flex flex-col items-center">
            <div className="mb-3 text-white">
              <OpenAILogoGlyph className="w-11 h-11 stroke-[1.6]" />
            </div>

            <h2 className="text-[22px] font-bold text-white tracking-tight mb-5 text-center">
              How can I help you today?
            </h2>

            {/* Prompt Box */}
            <form
              onSubmit={(e) => {
                e.preventDefault();
                handleSendMessage();
              }}
              className="w-full bg-[#202634] border border-[#313B4E] rounded-2xl p-3 shadow-lg"
            >
              <input
                type="text"
                value={promptText}
                onChange={(e) => setPromptText(e.target.value)}
                placeholder="Message ChatGPT..."
                className="w-full bg-transparent text-sm text-slate-100 placeholder:text-slate-400 focus:outline-none mb-3 px-1"
              />

              <div className="flex items-center justify-between">
                <div className="flex items-center gap-1.5">
                  <button
                    type="button"
                    onClick={() => handleSendMessage('Analyze my Two Sum TypeScript implementation')}
                    title="Insert Code Snippet"
                    className="w-7 h-7 rounded-full border border-[#38445A] hover:bg-[#2A3346] flex items-center justify-center text-slate-300 transition-colors cursor-pointer"
                  >
                    <Plus className="w-3.5 h-3.5" />
                  </button>
                  <button
                    type="button"
                    onClick={() => setWebSearchActive((v) => !v)}
                    title="Toggle Documentation Grounding"
                    className={`w-7 h-7 rounded-full border flex items-center justify-center transition-colors cursor-pointer ${
                      webSearchActive
                        ? 'bg-blue-500/20 border-blue-400 text-blue-300'
                        : 'border-[#38445A] hover:bg-[#2A3346] text-slate-300'
                    }`}
                  >
                    <Globe className="w-3.5 h-3.5" />
                  </button>
                  <button
                    type="button"
                    onClick={() => setReasonModeActive((v) => !v)}
                    title="Toggle Step-by-Step Complexity Reasoning"
                    className={`w-7 h-7 rounded-full border flex items-center justify-center transition-colors cursor-pointer ${
                      reasonModeActive
                        ? 'bg-amber-500/20 border-amber-400 text-amber-300'
                        : 'border-[#38445A] hover:bg-[#2A3346] text-slate-300'
                    }`}
                  >
                    <Lightbulb className="w-3.5 h-3.5" />
                  </button>
                </div>

                <button
                  type="submit"
                  className="w-7 h-7 rounded-full bg-[#3A4458] hover:bg-slate-200 hover:text-slate-900 text-slate-200 flex items-center justify-center transition-colors cursor-pointer"
                  title="Send Message"
                >
                  <ArrowUp className="w-4 h-4" />
                </button>
              </div>
            </form>

            {/* Quick Suggestion Buttons */}
            <div className="flex flex-wrap items-center justify-center gap-2 mt-4">
              <button
                type="button"
                onClick={() => handleSendMessage('Explain a DSA concept')}
                className="px-3 py-1.5 rounded-full bg-[#1C2230] hover:bg-[#252D3F] border border-[#2E384D] text-[11px] font-medium text-slate-200 transition-colors cursor-pointer whitespace-nowrap"
              >
                Explain a DSA concept
              </button>
              <button
                type="button"
                onClick={() => handleSendMessage('Help me solve a problem')}
                className="px-3 py-1.5 rounded-full bg-[#1C2230] hover:bg-[#252D3F] border border-[#2E384D] text-[11px] font-medium text-slate-200 transition-colors cursor-pointer whitespace-nowrap"
              >
                Help me solve a problem
              </button>
              <button
                type="button"
                onClick={() => handleSendMessage('Review my code')}
                className="px-3 py-1.5 rounded-full bg-[#1C2230] hover:bg-[#252D3F] border border-[#2E384D] text-[11px] font-medium text-slate-200 transition-colors cursor-pointer whitespace-nowrap"
              >
                Review my code
              </button>
              <button
                type="button"
                onClick={() => handleSendMessage('How to solve Two Sum in O(n)?')}
                title="More Study Prompts"
                className="w-7 h-7 rounded-full bg-[#1C2230] hover:bg-[#252D3F] border border-[#2E384D] flex items-center justify-center text-slate-300 transition-colors cursor-pointer"
              >
                <MoreHorizontal className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
        ) : (
          /* Active Conversation Thread View */
          <div className="flex flex-col h-full justify-between max-w-2xl w-full mx-auto">
            <div className="space-y-4 overflow-y-auto pr-1 pb-4">
              <div className="flex items-center justify-between border-b border-[#252E40] pb-2">
                <div className="text-xs font-semibold text-slate-200 flex items-center gap-1.5">
                  <Sparkles className="w-3.5 h-3.5 text-emerald-400" />
                  <span>{activeThread.title}</span>
                </div>
                <button
                  type="button"
                  onClick={() => setActiveThreadId(null)}
                  className="text-[11px] text-slate-400 hover:text-white cursor-pointer"
                >
                  ← Back to Prompt Home
                </button>
              </div>

              {activeThread.messages.map((msg) => (
                <div
                  key={msg.id}
                  className={`rounded-xl p-3 text-xs leading-relaxed ${
                    msg.role === 'user'
                      ? 'bg-[#232B3B] text-white ml-8 border border-[#323D52]'
                      : 'bg-[#1B2230] text-slate-200 mr-4 border border-[#263044]'
                  }`}
                >
                  <div className="text-[10px] font-semibold text-slate-400 mb-1">
                    {msg.role === 'user' ? 'You' : 'ChatGPT Study Assistant'}
                  </div>
                  <div className="whitespace-pre-line">{msg.content}</div>
                  {msg.codeSnippet && (
                    <pre className="mt-2.5 p-2.5 rounded-lg bg-[#0D1119] border border-[#222B3C] font-mono text-[11px] text-emerald-300 overflow-x-auto">
                      <code>{msg.codeSnippet}</code>
                    </pre>
                  )}
                </div>
              ))}
            </div>

            <form
              onSubmit={(e) => {
                e.preventDefault();
                handleSendMessage();
              }}
              className="flex items-center gap-2 bg-[#202634] border border-[#313B4E] rounded-xl px-3 py-2 mt-2 shrink-0"
            >
              <input
                type="text"
                value={promptText}
                onChange={(e) => setPromptText(e.target.value)}
                placeholder="Ask follow-up question..."
                className="flex-1 bg-transparent text-xs text-slate-100 placeholder:text-slate-400 focus:outline-none"
              />
              <button
                type="submit"
                className="w-6 h-6 rounded-full bg-blue-600 hover:bg-blue-500 text-white flex items-center justify-center cursor-pointer"
              >
                <ArrowUp className="w-3.5 h-3.5" />
              </button>
            </form>
          </div>
        )}
      </main>
    </div>
  );
};
