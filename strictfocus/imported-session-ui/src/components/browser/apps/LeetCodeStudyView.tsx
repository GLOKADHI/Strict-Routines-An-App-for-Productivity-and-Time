/**
 * StrictRecords — Interactive LeetCode Study & Problem Solver View
 *
 * Matches the right pane in the reference screenshot (`Problems` table, category tabs,
 * search & filter bar, and interactive code runner when a problem is clicked).
 */

import React, { useState } from 'react';
import {
  Bell,
  Check,
  ChevronDown,
  LayoutGrid,
  List,
  Play,
  RotateCcw,
  Search,
} from 'lucide-react';

export interface LeetCodeProblem {
  id: number;
  title: string;
  acceptance: string;
  difficulty: 'Easy' | 'Medium' | 'Hard';
  category: 'Algorithms' | 'Database' | 'Shell' | 'Concurrency' | 'System Design';
  tags: string[];
  solved: boolean;
  description: string;
  starterCode: string;
}

const INITIAL_PROBLEMS: LeetCodeProblem[] = [
  {
    id: 1,
    title: 'Two Sum',
    acceptance: '49.3%',
    difficulty: 'Easy',
    category: 'Algorithms',
    tags: ['Array', 'Hash Table'],
    solved: true,
    description:
      'Given an array of integers nums and an integer target, return indices of the two numbers such that they add up to target. You may assume that each input would have exactly one solution.',
    starterCode: `function twoSum(nums: number[], target: number): number[] {\n  const map = new Map<number, number>();\n  for (let i = 0; i < nums.length; i++) {\n    const complement = target - nums[i];\n    if (map.has(complement)) return [map.get(complement)!, i];\n    map.set(nums[i], i);\n  }\n  return [];\n}`,
  },
  {
    id: 2,
    title: 'Add Two Numbers',
    acceptance: '42.7%',
    difficulty: 'Medium',
    category: 'Algorithms',
    tags: ['Linked List', 'Math'],
    solved: false,
    description:
      'You are given two non-empty linked lists representing two non-negative integers. The digits are stored in reverse order, and each of their nodes contains a single digit. Add the two numbers and return the sum as a linked list.',
    starterCode: `function addTwoNumbers(l1: ListNode | null, l2: ListNode | null): ListNode | null {\n  // Write your O(max(m, n)) solution here\n  return null;\n}`,
  },
  {
    id: 3,
    title: 'Longest Substring Without Repeating Characters',
    acceptance: '36.1%',
    difficulty: 'Medium',
    category: 'Algorithms',
    tags: ['Hash Table', 'String', 'Sliding Window'],
    solved: false,
    description:
      'Given a string s, find the length of the longest substring without repeating characters.',
    starterCode: `function lengthOfLongestSubstring(s: string): number {\n  let left = 0, maxLen = 0;\n  const seen = new Map<string, number>();\n  for (let right = 0; right < s.length; right++) {\n    if (seen.has(s[right])) {\n      left = Math.max(left, seen.get(s[right])! + 1);\n    }\n    seen.set(s[right], right);\n    maxLen = Math.max(maxLen, right - left + 1);\n  }\n  return maxLen;\n}`,
  },
  {
    id: 4,
    title: 'Median of Two Sorted Arrays',
    acceptance: '34.8%',
    difficulty: 'Hard',
    category: 'Algorithms',
    tags: ['Array', 'Binary Search', 'Divide and Conquer'],
    solved: false,
    description:
      'Given two sorted arrays nums1 and nums2 of size m and n respectively, return the median of the two sorted arrays. The overall run time complexity should be O(log (m+n)).',
    starterCode: `function findMedianSortedArrays(nums1: number[], nums2: number[]): number {\n  // Binary search partition cut on smaller array\n  return 2.0;\n}`,
  },
  {
    id: 5,
    title: 'Longest Palindromic Substring',
    acceptance: '33.9%',
    difficulty: 'Medium',
    category: 'Algorithms',
    tags: ['String', 'Dynamic Programming'],
    solved: false,
    description:
      'Given a string s, return the longest palindromic substring in s.',
    starterCode: `function longestPalindrome(s: string): string {\n  // Expand around center in O(n^2) time, O(1) space\n  return s.slice(0, 1);\n}`,
  },
];

const CATEGORIES = [
  'All Problems',
  'Algorithms',
  'Database',
  'Shell',
  'Concurrency',
  'System Design',
] as const;

export const LeetCodeLogoGlyph: React.FC<{ className?: string }> = ({
  className = 'w-4 h-4',
}) => (
  <svg viewBox="0 0 24 24" fill="none" className={className}>
    <path
      d="M13.5 3.5L6 11l7.5 7.5"
      stroke="#FFA116"
      strokeWidth="2.4"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
    <path
      d="M10 14.5h9"
      stroke="#E2E8F0"
      strokeWidth="2.4"
      strokeLinecap="round"
    />
  </svg>
);

export const LeetCodeStudyView: React.FC = () => {
  const [problems, setProblems] = useState<LeetCodeProblem[]>(INITIAL_PROBLEMS);
  const [activeNav, setActiveNav] = useState<'Problems' | 'Contest' | 'Discuss' | 'Interview'>('Problems');
  const [activeCategory, setActiveCategory] = useState<string>('All Problems');
  const [searchQuery, setSearchQuery] = useState('');
  const [difficultyFilter, setDifficultyFilter] = useState<'ALL' | 'Easy' | 'Medium' | 'Hard'>('ALL');
  const [statusFilter, setStatusFilter] = useState<'ALL' | 'SOLVED' | 'TODO'>('ALL');
  const [selectedProblemId, setSelectedProblemId] = useState<number | null>(null);
  const [editorCode, setEditorCode] = useState<string>('');
  const [testOutput, setTestOutput] = useState<string | null>(null);

  const selectedProblem = problems.find((p) => p.id === selectedProblemId) || null;

  const filteredProblems = problems.filter((p) => {
    const matchesCategory =
      activeCategory === 'All Problems' || p.category === activeCategory;
    const matchesDifficulty =
      difficultyFilter === 'ALL' || p.difficulty === difficultyFilter;
    const matchesStatus =
      statusFilter === 'ALL' ||
      (statusFilter === 'SOLVED' && p.solved) ||
      (statusFilter === 'TODO' && !p.solved);
    const matchesSearch =
      !searchQuery.trim() ||
      p.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
      String(p.id).includes(searchQuery.trim()) ||
      p.tags.some((t) => t.toLowerCase().includes(searchQuery.toLowerCase()));

    return matchesCategory && matchesDifficulty && matchesStatus && matchesSearch;
  });

  const handleOpenProblem = (problem: LeetCodeProblem) => {
    setSelectedProblemId(problem.id);
    setEditorCode(problem.starterCode);
    setTestOutput(null);
  };

  const handleRunTests = () => {
    if (!selectedProblem) return;
    setTestOutput(
      `Accepted · Runtime: 52 ms (Beats 94.20%) · Memory: 44.1 MB\nTest Cases Passed: 3 / 3`
    );
    setProblems((prev) =>
      prev.map((p) => (p.id === selectedProblem.id ? { ...p, solved: true } : p))
    );
  };

  const getDifficultyBadge = (difficulty: LeetCodeProblem['difficulty']) => {
    switch (difficulty) {
      case 'Easy':
        return 'bg-[#122D24] text-[#22C55E] border-[#22C55E]/30';
      case 'Medium':
        return 'bg-[#2E2412] text-[#F59E0B] border-[#F59E0B]/30';
      case 'Hard':
        return 'bg-[#2F161B] text-[#EF4444] border-[#EF4444]/30';
    }
  };

  return (
    <div className="h-full w-full flex flex-col bg-[#12161F] text-slate-100 overflow-hidden select-text">
      {/* Top LeetCode Navigation Bar */}
      <div className="h-12 bg-[#161B26] border-b border-[#222B3B] px-5 flex items-center justify-between shrink-0">
        <div className="flex items-center gap-6">
          <button
            type="button"
            onClick={() => setSelectedProblemId(null)}
            className="flex items-center gap-2 cursor-pointer"
          >
            <LeetCodeLogoGlyph className="w-5 h-5" />
            <span className="text-[15px] font-bold text-white tracking-tight">
              LeetCode
            </span>
          </button>

          <nav className="flex items-center gap-5 text-xs font-medium">
            {(['Problems', 'Contest', 'Discuss', 'Interview'] as const).map((item) => {
              const isActive = activeNav === item;
              return (
                <button
                  key={item}
                  type="button"
                  onClick={() => {
                    setActiveNav(item);
                    setSelectedProblemId(null);
                  }}
                  className={`relative py-3.5 transition-colors flex items-center gap-1 cursor-pointer whitespace-nowrap ${
                    isActive
                      ? 'text-[#3B82F6] font-semibold'
                      : 'text-slate-300 hover:text-white'
                  }`}
                >
                  <span>{item}</span>
                  {item === 'Interview' && <ChevronDown className="w-3 h-3" />}
                  {isActive && (
                    <span className="absolute bottom-0 left-0 right-0 h-[2.5px] bg-[#3B82F6] rounded-full" />
                  )}
                </button>
              );
            })}
          </nav>
        </div>

        {/* Right Nav Controls (Store, Bell, User Avatar) */}
        <div className="flex items-center gap-4">
          <button
            type="button"
            className="text-xs text-slate-300 hover:text-white flex items-center gap-1 cursor-pointer"
          >
            <span>Store</span>
            <ChevronDown className="w-3 h-3" />
          </button>
          <button
            type="button"
            title="Notifications"
            className="text-slate-300 hover:text-white cursor-pointer"
          >
            <Bell className="w-4 h-4" />
          </button>
          <div className="w-6 h-6 rounded-full bg-[#3B4C6B] text-white text-xs font-semibold flex items-center justify-center">
            S
          </div>
        </div>
      </div>

      {/* Main Content Area */}
      {selectedProblem ? (
        /* Interactive Problem Solver & Code Editor View */
        <div className="flex-1 flex flex-col p-4 overflow-y-auto space-y-3">
          <div className="flex items-center justify-between border-b border-[#222B3B] pb-2.5">
            <div className="flex items-center gap-2.5">
              <button
                type="button"
                onClick={() => setSelectedProblemId(null)}
                className="px-2.5 py-1 rounded bg-[#1C2433] hover:bg-[#253044] text-xs text-slate-200 border border-[#2D3A52] cursor-pointer"
              >
                ← Problem List
              </button>
              <h3 className="text-sm font-bold text-white">
                {selectedProblem.id}. {selectedProblem.title}
              </h3>
              <span
                className={`px-2 py-0.5 rounded text-[11px] font-semibold border ${getDifficultyBadge(
                  selectedProblem.difficulty
                )}`}
              >
                {selectedProblem.difficulty}
              </span>
            </div>

            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setEditorCode(selectedProblem.starterCode)}
                className="px-2.5 py-1 rounded bg-[#1C2433] hover:bg-[#253044] text-xs text-slate-300 flex items-center gap-1 cursor-pointer"
              >
                <RotateCcw className="w-3 h-3" />
                <span>Reset</span>
              </button>
              <button
                type="button"
                onClick={handleRunTests}
                className="px-3 py-1 rounded bg-[#22C55E] hover:bg-emerald-500 text-slate-950 font-semibold text-xs flex items-center gap-1.5 cursor-pointer"
              >
                <Play className="w-3.5 h-3.5 fill-current" />
                <span>Run & Submit</span>
              </button>
            </div>
          </div>

          <p className="text-xs text-slate-300 leading-relaxed bg-[#171D29] p-3 rounded-lg border border-[#222B3B]">
            {selectedProblem.description}
          </p>

          <div className="flex-1 flex flex-col">
            <div className="text-[11px] font-mono text-slate-400 mb-1">
              TypeScript (Node 20 · Strict Mode)
            </div>
            <textarea
              value={editorCode}
              onChange={(e) => setEditorCode(e.target.value)}
              spellCheck={false}
              className="flex-1 min-h-[150px] w-full bg-[#0C1017] border border-[#232D3F] rounded-lg p-3 font-mono text-xs text-emerald-300 focus:outline-none focus:border-blue-500 resize-none"
            />
          </div>

          {testOutput && (
            <div className="bg-[#0E221B] border border-[#22C55E]/40 rounded-lg p-3 font-mono text-xs text-[#22C55E] whitespace-pre-line">
              {testOutput}
            </div>
          )}
        </div>
      ) : (
        /* Default Problems Table View matching screenshot */
        <div className="flex-1 px-5 py-4 overflow-y-auto">
          {/* Heading + View Toggle */}
          <div className="flex items-center justify-between mb-3">
            <h1 className="text-[20px] font-bold text-white tracking-tight">
              Problems
            </h1>
            <div className="flex items-center bg-[#1B2230] border border-[#283347] rounded-lg p-0.5">
              <button
                type="button"
                className="p-1.5 rounded bg-[#263145] text-white cursor-pointer"
                title="Table View"
              >
                <List className="w-3.5 h-3.5" />
              </button>
              <button
                type="button"
                className="p-1.5 rounded text-slate-400 hover:text-slate-200 cursor-pointer"
                title="Compact View"
              >
                <LayoutGrid className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>

          {/* Category Filter Tabs */}
          <div className="flex items-center gap-5 border-b border-[#222B3B] mb-3.5 overflow-x-auto">
            {CATEGORIES.map((cat) => {
              const isSelected = activeCategory === cat;
              return (
                <button
                  key={cat}
                  type="button"
                  onClick={() => setActiveCategory(cat)}
                  className={`relative pb-2.5 text-xs font-medium transition-colors whitespace-nowrap cursor-pointer ${
                    isSelected
                      ? 'text-white font-semibold'
                      : 'text-slate-400 hover:text-slate-200'
                  }`}
                >
                  <span>{cat}</span>
                  {isSelected && (
                    <span className="absolute bottom-0 left-0 right-0 h-[2px] bg-[#3B82F6] rounded-full" />
                  )}
                </button>
              );
            })}
          </div>

          {/* Search & Dropdown Filters Row */}
          <div className="flex flex-wrap items-center justify-between gap-2.5 mb-4">
            <div className="relative flex-1 min-w-[210px]">
              <Search className="w-3.5 h-3.5 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search problems, tags, or difficulty..."
                className="w-full bg-[#19202E] border border-[#283347] rounded-full pl-8 pr-3.5 py-1.5 text-xs text-slate-100 placeholder:text-slate-400 focus:outline-none focus:border-blue-500"
              />
            </div>

            <div className="flex items-center gap-2">
              {/* Difficulty Cycle Button */}
              <button
                type="button"
                onClick={() => {
                  const order: ('ALL' | 'Easy' | 'Medium' | 'Hard')[] = [
                    'ALL',
                    'Easy',
                    'Medium',
                    'Hard',
                  ];
                  const next =
                    order[(order.indexOf(difficultyFilter) + 1) % order.length];
                  setDifficultyFilter(next);
                }}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-[#19202E] hover:bg-[#222B3D] border border-[#283347] text-xs text-slate-200 cursor-pointer whitespace-nowrap"
              >
                <span>
                  {difficultyFilter === 'ALL' ? 'Difficulty' : difficultyFilter}
                </span>
                <ChevronDown className="w-3 h-3 text-slate-400" />
              </button>

              {/* Tags Button */}
              <button
                type="button"
                onClick={() =>
                  setSearchQuery((q) => (q === 'Array' ? '' : 'Array'))
                }
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-[#19202E] hover:bg-[#222B3D] border border-[#283347] text-xs text-slate-200 cursor-pointer whitespace-nowrap"
              >
                <span>Tags</span>
                <ChevronDown className="w-3 h-3 text-slate-400" />
              </button>

              {/* Status Filter Button */}
              <button
                type="button"
                onClick={() => {
                  const order: ('ALL' | 'SOLVED' | 'TODO')[] = [
                    'ALL',
                    'SOLVED',
                    'TODO',
                  ];
                  const next = order[(order.indexOf(statusFilter) + 1) % order.length];
                  setStatusFilter(next);
                }}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-[#19202E] hover:bg-[#222B3D] border border-[#283347] text-xs text-slate-200 cursor-pointer whitespace-nowrap"
              >
                <span>
                  {statusFilter === 'ALL'
                    ? 'Status'
                    : statusFilter === 'SOLVED'
                      ? 'Solved'
                      : 'Todo'}
                </span>
                <ChevronDown className="w-3 h-3 text-slate-400" />
              </button>
            </div>
          </div>

          {/* Problems Table */}
          <div className="w-full">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="border-b border-[#222B3B] text-[11px] font-medium text-slate-400">
                  <th className="py-2.5 w-8"></th>
                  <th className="py-2.5 w-10">#</th>
                  <th className="py-2.5">Title</th>
                  <th className="py-2.5 w-24 text-right pr-4">Acceptance</th>
                  <th className="py-2.5 w-24 text-right">Difficulty</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#1E2636] text-[13px]">
                {filteredProblems.map((prob) => (
                  <tr
                    key={prob.id}
                    onClick={() => handleOpenProblem(prob)}
                    className="hover:bg-[#192130] transition-colors cursor-pointer group"
                  >
                    <td className="py-3 pl-1">
                      {prob.solved && (
                        <Check className="w-4 h-4 text-[#22C55E] stroke-[2.6]" />
                      )}
                    </td>
                    <td className="py-3 text-slate-300 font-mono tabular-nums">
                      {prob.id}
                    </td>
                    <td className="py-3 font-medium text-slate-100 group-hover:text-blue-400 transition-colors">
                      {prob.title}
                    </td>
                    <td className="py-3 text-right pr-4 text-slate-300 font-mono tabular-nums">
                      {prob.acceptance}
                    </td>
                    <td className="py-3 text-right">
                      <span
                        className={`inline-block px-2.5 py-0.5 rounded-md text-xs font-semibold border ${getDifficultyBadge(
                          prob.difficulty
                        )}`}
                      >
                        {prob.difficulty}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
};
