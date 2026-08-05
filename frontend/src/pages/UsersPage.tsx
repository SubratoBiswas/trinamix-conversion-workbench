import React, { useCallback, useEffect, useState } from "react";
import { KeyRound, ShieldCheck, UserPlus, UsersRound } from "lucide-react";
import { UsersApi } from "@/api";
import type { UserAccount } from "@/api";
import { useAuth } from "@/store/authStore";
import {
  Button, Card, CardBody, CardHeader, EmptyState, Modal, PageLoader, PageTitle, Pill,
} from "@/components/ui/Primitives";

/**
 * Users and roles.
 *
 * The last piece of the Admin / Normal split. Enforcement has been in place for
 * a while — every API route is mounted behind a section in
 * `services/access_control.py` — but there was no way to MAKE somebody a Normal
 * user short of editing Mongo by hand, so every account was still an
 * administrator and none of it applied to anyone. This screen is that missing
 * control, and nothing more: it is not where the permission is decided.
 *
 * Two things it deliberately cannot do, both because the API refuses them and
 * this screen only declines to offer a button the API would reject:
 *
 *   - Set a password. Inviting creates the account and assigns its role; the
 *     credential is a human action, out of band. Until then the row reads
 *     "no password" and that account cannot sign in at all.
 *   - Change your own role, or demote the last administrator. Either one ends
 *     with nobody able to reach this screen, and the only remaining repair is
 *     the hand-edited Mongo document this screen was built to retire.
 *
 * A role change takes effect on the person's NEXT REQUEST, not their next
 * sign-in — the backend re-reads the account per request rather than trusting
 * the role baked into their token.
 */

const ROLE_COPY: Record<string, string> = {
  admin: "Everything, including this screen.",
  normal: "Home, Conversion Workbench and Load Management.",
};

const RoleBadge: React.FC<{ role: string }> = ({ role }) => {
  const r = (role || "").trim().toLowerCase();
  return r === "admin"
    ? <Pill tone="brand"><ShieldCheck className="mr-1 inline h-3 w-3" />Administrator</Pill>
    : <Pill tone="neutral">Normal</Pill>;
};

export const UsersPage: React.FC = () => {
  const me = useAuth((s) => s.user);
  const [rows, setRows] = useState<UserAccount[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const [inviteOpen, setInviteOpen] = useState(false);
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("normal");
  const [inviting, setInviting] = useState(false);
  const [inviteErr, setInviteErr] = useState<string | null>(null);

  const refresh = useCallback(
    () => UsersApi.list().then((r) => { setRows(r); setErr(null); })
      .catch((e: any) => {
        setRows([]);
        setErr(e?.response?.data?.detail ?? "Could not load the user list.");
      }),
    [],
  );
  useEffect(() => { void refresh(); }, [refresh]);

  const invite = async () => {
    setInviting(true); setInviteErr(null);
    try {
      await UsersApi.invite({ name: name.trim(), email: email.trim(), role });
      setInviteOpen(false); setName(""); setEmail(""); setRole("normal");
      await refresh();
    } catch (e: any) {
      setInviteErr(e?.response?.data?.detail ?? "Could not create that account.");
    } finally {
      setInviting(false);
    }
  };

  // The API is the one that refuses; this reports what it said rather than
  // guessing. A screen that predicted the rule would be a second copy of it, and
  // a second copy is how a rule fixed once stays broken.
  const changeRole = async (u: UserAccount, next: string) => {
    setBusyId(u.id); setErr(null);
    try {
      await UsersApi.setRole(u.id, next);
      await refresh();
    } catch (e: any) {
      setErr(e?.response?.data?.detail ?? "Could not change that role.");
    } finally {
      setBusyId(null);
    }
  };

  if (!rows) return <PageLoader label="Loading users…" />;

  const admins = rows.filter((u) => (u.role || "").trim().toLowerCase() === "admin");
  const pending = rows.filter((u) => !u.password_set);

  return (
    <>
      <PageTitle
        title="Users"
        subtitle="Who can sign in, and how much of the workbench they see. A Normal user keeps Home, Conversion Workbench and Load Management; everything else — and every API behind it — is administrators only."
        right={
          <Button onClick={() => setInviteOpen(true)}>
            <UserPlus className="mr-1 h-4 w-4" /> Invite user
          </Button>
        }
      />

      {err && (
        <div className="mb-4 rounded-lg border border-danger/30 bg-danger-subtle p-3 text-sm text-danger">
          {err}
        </div>
      )}

      {pending.length > 0 && (
        <Card className="mb-5 border-warning/30">
          <CardBody className="flex items-start gap-3">
            <KeyRound className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
            <div className="text-[12.5px] text-ink-muted">
              <span className="font-semibold text-ink">
                {pending.length} account{pending.length === 1 ? "" : "s"} cannot sign in yet.
              </span>{" "}
              Inviting somebody creates the account and its role but not a password — that
              stays a human action. Set one for them the same way this install's first
              administrator was created, and they are in.
            </div>
          </CardBody>
        </Card>
      )}

      <Card>
        <CardHeader
          title={<span className="inline-flex items-center gap-1.5"><UsersRound className="h-4 w-4 text-brand" /> Accounts</span>}
          subtitle={`${rows.length} account${rows.length === 1 ? "" : "s"} · ${admins.length} administrator${admins.length === 1 ? "" : "s"}`}
        />
        <CardBody className="p-0">
          {rows.length === 0 ? (
            <EmptyState
              title="No accounts"
              description="Nobody can sign in. Invite somebody to get started."
              icon={<UsersRound className="h-5 w-5" />}
            />
          ) : (
            rows.map((u) => {
              const isOnlyAdmin =
                admins.length <= 1 && (u.role || "").trim().toLowerCase() === "admin";
              const locked = u.is_self || isOnlyAdmin;
              return (
                <div
                  key={u.id}
                  className="flex flex-wrap items-center gap-3 border-b border-line px-5 py-3 last:border-0"
                >
                  <div className="min-w-[220px] flex-1">
                    <div className="flex items-center gap-1.5 text-sm font-semibold text-ink">
                      {u.name}
                      {u.is_self && (
                        <span className="text-[10px] font-medium uppercase tracking-wider text-ink-muted">
                          you
                        </span>
                      )}
                    </div>
                    <div className="font-mono text-[11px] text-ink-muted">{u.email}</div>
                  </div>

                  <div className="flex items-center gap-1.5">
                    <RoleBadge role={u.role} />
                    {!u.password_set && <Pill tone="warning">no password</Pill>}
                  </div>

                  <div className="w-[240px] text-right">
                    {locked ? (
                      <span className="text-[11px] text-ink-muted">
                        {u.is_self
                          ? "Another administrator changes your role"
                          : "The only administrator"}
                      </span>
                    ) : (
                      <select
                        value={(u.role || "").trim().toLowerCase()}
                        disabled={busyId === u.id}
                        onChange={(e) => void changeRole(u, e.target.value)}
                        className="rounded-lg border border-line px-2.5 py-1.5 text-[12.5px]"
                      >
                        <option value="normal">Normal</option>
                        <option value="admin">Administrator</option>
                      </select>
                    )}
                  </div>
                </div>
              );
            })
          )}
        </CardBody>
      </Card>

      <p className="mt-3 text-[11px] text-ink-muted">
        Signed in as {me?.email ?? "an unknown account"}. A role change applies on that
        person's next request — they do not need to sign out and back in.
      </p>

      <Modal
        open={inviteOpen}
        onClose={() => setInviteOpen(false)}
        size="md"
        title="Invite a user"
        footer={
          <>
            <Button variant="ghost" onClick={() => setInviteOpen(false)}>Cancel</Button>
            <Button
              onClick={() => void invite()}
              loading={inviting}
              disabled={!name.trim() || !email.trim()}
            >
              Create account
            </Button>
          </>
        }
      >
        <div className="space-y-3 text-sm">
          <p className="text-ink-muted">
            This creates the account and its role. It does not set a password, so the
            person cannot sign in until somebody gives them one — creating credentials
            stays a human action.
          </p>
          <div>
            <div className="mb-1 text-xs font-semibold text-ink">Name</div>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Priya Raman"
              className="w-full rounded-lg border border-line px-3 py-2 text-sm"
            />
          </div>
          <div>
            <div className="mb-1 text-xs font-semibold text-ink">Email</div>
            <input
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="e.g. priya@nextpower.com"
              className="w-full rounded-lg border border-line px-3 py-2 text-sm"
            />
          </div>
          <div>
            <div className="mb-1 text-xs font-semibold text-ink">Role</div>
            <select
              value={role}
              onChange={(e) => setRole(e.target.value)}
              className="w-full rounded-lg border border-line px-3 py-2 text-sm"
            >
              <option value="normal">Normal</option>
              <option value="admin">Administrator</option>
            </select>
            <div className="mt-1 text-[11px] text-ink-muted">{ROLE_COPY[role]}</div>
          </div>
          {inviteErr && (
            <div className="rounded-lg border border-danger/30 bg-danger-subtle p-2.5 text-xs text-danger">
              {inviteErr}
            </div>
          )}
        </div>
      </Modal>
    </>
  );
};

export default UsersPage;
