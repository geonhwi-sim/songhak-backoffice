import type { Metadata } from "next";
import { Black_Han_Sans, Geist_Mono, IBM_Plex_Sans_KR } from "next/font/google";
import "./globals.css";

// 발주 앱·정산 앱과 같은 글꼴: 본문 IBM Plex Sans KR, 제목 Black Han Sans
const sans = IBM_Plex_Sans_KR({ variable: "--font-sans", subsets: ["latin"], weight: ["400", "500", "600", "700"] });
const display = Black_Han_Sans({ variable: "--font-display", subsets: ["latin"], weight: "400" });
const mono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "송학구이 백오피스",
  description: "정산·인건비·발주를 한곳에서 보는 관리자 화면",
  robots: { index: false, follow: false },
};

export const viewport = { themeColor: "#F4EEE6" };

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="ko" className={`${sans.variable} ${display.variable} ${mono.variable} h-full antialiased`}>
      <body className="min-h-full flex flex-col">{children}</body>
    </html>
  );
}
