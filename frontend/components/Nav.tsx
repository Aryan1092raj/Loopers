"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import UserMenu from "./UserMenu";

const LINKS = [
  ["/", "Chat"],
  ["/quiz", "Quiz"],
  ["/report", "Report"],
  ["/dashboard", "Dashboard"],
  ["/eval", "Evaluation"],
] as const;

export default function Nav() {
  const path = usePathname();
  return (
    <nav aria-label="Main" className="mx-auto flex w-full max-w-[96rem] items-center gap-4 px-3 py-2.5 sm:px-5">
      <Link href="/" className="flex items-center gap-2.5 font-semibold tracking-tight">
        <span aria-hidden className="grid h-8 w-8 place-items-center rounded-xl bg-gradient-to-br from-indigo-500 to-violet-500 text-sm text-white shadow-md">∞</span>
        Loopers
      </Link>
      <div className="ml-auto flex items-center gap-1 sm:gap-1.5">
        {LINKS.map(([href, text]) => (
          <Link
            key={href}
            href={href}
            aria-current={path === href ? "page" : undefined}
            className={`px-2 py-1.5 text-sm transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-indigo-600 ${path === href ? "font-semibold text-indigo-700 underline decoration-2 underline-offset-4" : "text-slate-700 hover:text-indigo-700"}`}
          >
            {text}
          </Link>
        ))}
        <UserMenu />
      </div>
    </nav>
  );
}
