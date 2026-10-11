"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import UserMenu from "./UserMenu";

export default function Nav() {
  const path = usePathname();
  return (
    <nav aria-label="Main" className="mx-auto flex w-full max-w-[96rem] items-center gap-4 px-3 py-2 sm:px-4">
      <Link href="/" className="flex items-center gap-2 font-semibold tracking-tight">
        <span aria-hidden className="grid h-7 w-7 place-items-center rounded-lg bg-gradient-to-br from-indigo-500 to-violet-500 text-sm text-white">∞</span>
        Loopers
      </Link>
      <div className="ml-auto flex items-center gap-3">
        <Link
          href="/quiz"
          aria-current={path === "/quiz" ? "page" : undefined}
          className={`rounded-full px-3 py-1.5 text-sm transition hover:bg-indigo-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-600 ${path === "/quiz" ? "font-semibold text-indigo-700" : "text-slate-700"}`}
        >
          Quiz
        </Link>
        <Link
          href="/eval"
          aria-current={path === "/eval" ? "page" : undefined}
          className={`rounded-full px-3 py-1.5 text-sm transition hover:bg-indigo-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-600 ${path === "/eval" ? "font-semibold text-indigo-700" : "text-slate-700"}`}
        >
          Evaluation
        </Link>
        <UserMenu />
      </div>
    </nav>
  );
}
