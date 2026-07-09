export { default } from "next-auth/middleware";

// Protect every page except the login screen and NextAuth's own API routes.
export const config = {
  matcher: ["/((?!api/auth|login|_next/static|_next/image|favicon.ico).*)"],
};
