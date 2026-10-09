import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { NotificationProvider } from "../src/notifications/notification-center";

const sans = Inter({ variable: "--font-sans", subsets: ["latin", "cyrillic"], display: "swap" });
const mono = JetBrains_Mono({ variable: "--font-mono", subsets: ["latin", "cyrillic"], display: "swap" });

export const metadata: Metadata = {
  title: "Infrastructure Control",
  description: "Управление серверной инфраструктурой.",
  icons: { icon: "/favicon.svg", shortcut: "/favicon.svg" },
  openGraph: {
    title: "Infrastructure Control",
    description: "Управление серверной инфраструктурой.",
  },
  twitter: { card: "summary" },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="ru" className={`${sans.variable} ${mono.variable}`}><body><NotificationProvider>{children}</NotificationProvider></body></html>;
}
