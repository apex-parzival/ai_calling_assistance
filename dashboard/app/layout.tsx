import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Front Desk",
  description: "Calls handled by the clinic's AI receptionist",
  robots: { index: false, follow: false },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
