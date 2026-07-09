import type { AuthOptions } from "next-auth";
import CredentialsProvider from "next-auth/providers/credentials";

// Phase 1: a single shared password (DASHBOARD_PASSWORD), no per-user
// accounts yet. Built on NextAuth so adding real accounts later (a
// CredentialsProvider backed by a `users` table, or an OAuth provider) is a
// config change here, not a rewrite - middleware.ts and every page already
// gate on a NextAuth session, not on this specific provider.
export const authOptions: AuthOptions = {
  session: { strategy: "jwt" },
  pages: {
    signIn: "/login",
  },
  providers: [
    CredentialsProvider({
      name: "Password",
      credentials: {
        password: { label: "Password", type: "password" },
      },
      async authorize(credentials) {
        const expected = process.env.DASHBOARD_PASSWORD;
        if (!expected) {
          throw new Error(
            "DASHBOARD_PASSWORD is not set on the server - set it in Railway variables."
          );
        }
        if (credentials?.password === expected) {
          return { id: "owner", name: "Owner" };
        }
        return null;
      },
    }),
  ],
  callbacks: {
    async jwt({ token }) {
      return token;
    },
    async session({ session }) {
      return session;
    },
  },
};
