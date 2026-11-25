import { createContext, useContext } from 'react'

// Components show configuration controls only to the administrator. The server
// enforces the same rule; this just keeps buttons a member cannot use off the page.
const RoleContext = createContext({ admin: true, username: null })

export const RoleProvider = RoleContext.Provider

export function useRole() {
  return useContext(RoleContext)
}
