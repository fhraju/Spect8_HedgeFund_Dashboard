export function LogoutButton({ className = "" }: { className?: string }) {
  return (
    <form
      action="/api/auth/logout"
      className={`logout-form ${className}`.trim()}
      method="post"
    >
      <button className="logout-button" type="submit">
        <i aria-hidden="true">↪</i> Logout
      </button>
    </form>
  );
}
