import { cn } from "@/lib/utils";

/** 송학구이 표시: 숯 검정 사각형 안의 불씨. 발주 앱(songhak-inventory Header)·정산 앱과 같은 모양 */
export function BrandMark({ className }: { className?: string }) {
  return (
    <span
      className={cn("flex size-8 shrink-0 items-center justify-center rounded-[10px] bg-[#221b16] text-[#f97316]", className)}
    >
      <svg
        width="18"
        height="18"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.9"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
      >
        <path d="M12 3c1 3.5 5 5.5 5 10a5 5 0 0 1-10 0c0-2 1-3.5 2-4.5 0 2 1 3 2 3 0-3-1-5.5 1-8.5z" />
      </svg>
    </span>
  );
}
