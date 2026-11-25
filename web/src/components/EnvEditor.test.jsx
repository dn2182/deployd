import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import EnvEditor from './EnvEditor.jsx'
import { renderWith } from '../test-utils.jsx'

const STATUS = { path: '/etc/api/api.env', available: true, exists: true, owner: 'root', group: 'api', mode: '0640', backups: 0 }
const CONTENT = '# database\nDB_PASSWORD=hunter2\nexport API_URL=https://api.example.com\n'
const SERVICE = { restart: { command: ['sudo', 'systemctl', 'restart', 'api'] }, health: { url: 'http://127.0.0.1:5000/healthz' } }
const PREVIEW = { saved: false, would_save: true, added: [], removed: [], changed: ['DB_PASSWORD'], keys_before: 2, keys_after: 2, unquoted: [], duplicates: [] }

function backend(overrides = {}) {
  const routes = {
    'GET /admin/apps/api/env': STATUS,
    'GET /admin/apps/api/env/content': { ...STATUS, content: CONTENT, sha256: 'c'.repeat(64) },
    'POST /admin/apps/api/env/preview': PREVIEW,
    'PUT /admin/apps/api/env': { saved: true, deploy_id: null, added: [], removed: [], changed: ['DB_PASSWORD'] },
    'POST /admin/apps/api/env/restore': { saved: true, deploy_id: null },
    ...overrides,
  }
  return vi.fn(async (path, options = {}) => {
    const answer = routes[`${options.method ?? 'GET'} ${path}`]
    if (answer instanceof Error) throw answer
    return answer
  })
}

function setup(call = backend(), spec = SERVICE) {
  const onChanged = vi.fn()
  renderWith(<EnvEditor name="api" spec={spec} call={call} onChanged={onChanged} />)
  return { call, onChanged }
}

describe('env editor', () => {
  it('shows metadata first and loads values only on request, hidden', async () => {
    const { call } = setup()
    expect(await screen.findByText('/etc/api/api.env')).toBeInTheDocument()
    expect(screen.getByText(/Owner root:api, mode 0640/)).toBeInTheDocument()
    expect(call).toHaveBeenCalledTimes(1)
    expect(screen.queryByDisplayValue('hunter2')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Open file' }))
    const value = await screen.findByLabelText('Value of DB_PASSWORD')
    expect(value).toHaveAttribute('type', 'password')
    expect(screen.getByText('# database')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Show value of DB_PASSWORD' }))
    expect(value).toHaveAttribute('type', 'text')
    expect(screen.getByLabelText('Value of API_URL')).toHaveAttribute('type', 'password')
  })

  it('saves edits with the loaded digest, keeps untouched lines verbatim and queues a restart', async () => {
    const call = backend({ 'PUT /admin/apps/api/env': { saved: true, deploy_id: 'd'.repeat(32) } })
    const { onChanged } = setup(call)
    fireEvent.click(await screen.findByRole('button', { name: 'Open file' }))
    expect(await screen.findByRole('button', { name: 'Save file' })).toBeDisabled()
    fireEvent.change(screen.getByLabelText('Value of DB_PASSWORD'), { target: { value: 'n3w' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save file' }))
    fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Save file' }))
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1))
    expect(call).toHaveBeenCalledWith('/admin/apps/api/env', {
      method: 'PUT',
      body: JSON.stringify({
        content: '# database\nDB_PASSWORD=n3w\nexport API_URL=https://api.example.com\n',
        expected_sha256: 'c'.repeat(64),
        confirm_removed: [],
        restart: true,
      }),
    })
    expect(await screen.findByText(/Restart queued/)).toBeInTheDocument()
    expect(screen.queryByLabelText('Value of DB_PASSWORD')).not.toBeInTheDocument()
  })

  it('adds and removes variables, and static sites do not restart by default', async () => {
    const { call, onChanged } = setup(backend(), { restart: { command: ['/usr/bin/test', '-s', '/srv/x/index.html'] } })
    fireEvent.click(await screen.findByRole('button', { name: 'Open file' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Remove API_URL' }))
    fireEvent.click(screen.getByRole('button', { name: 'Add variable' }))
    const names = screen.getAllByLabelText('Variable name')
    fireEvent.change(names.at(-1), { target: { value: 'NEW_KEY' } })
    fireEvent.change(screen.getByLabelText('Value of NEW_KEY'), { target: { value: '1' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save file' }))
    fireEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'Save file' }))
    expect(await screen.findByText('Environment file saved.')).toBeInTheDocument()
    expect(JSON.parse(call.mock.calls.at(-2)[1].body)).toEqual({
      content: '# database\nDB_PASSWORD=hunter2\nNEW_KEY=1\n', expected_sha256: 'c'.repeat(64), confirm_removed: [], restart: false,
    })
    expect(onChanged).not.toHaveBeenCalled()
  })

  it('edits the raw text and converts back to fields', async () => {
    setup()
    fireEvent.click(await screen.findByRole('button', { name: 'Open file' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Edit as text' }))
    const area = screen.getByLabelText('File contents')
    expect(area).toHaveValue(CONTENT)
    fireEvent.change(area, { target: { value: 'ONLY=one\n' } })
    fireEvent.click(screen.getByRole('button', { name: 'Back to fields' }))
    expect(screen.getByLabelText('Value of ONLY')).toHaveValue('one')
    expect(screen.queryByLabelText('Value of DB_PASSWORD')).not.toBeInTheDocument()
  })

  it('keeps the editor open and shows the server message when a save is refused', async () => {
    setup(backend({ 'PUT /admin/apps/api/env': new Error('the file changed on the server after it was loaded; reload it first') }))
    fireEvent.click(await screen.findByRole('button', { name: 'Open file' }))
    fireEvent.change(await screen.findByLabelText('Value of DB_PASSWORD'), { target: { value: 'x' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save file' }))
    const dialog = await screen.findByRole('alertdialog')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save file' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent('reload it first')
    expect(screen.getByLabelText('Value of DB_PASSWORD')).toHaveValue('x')
  })

  it('lists removed variables and asks for the app name before saving', async () => {
    const call = backend({
      'POST /admin/apps/api/env/preview': { ...PREVIEW, changed: [], removed: ['DB_HOST', 'JWT_KEY'], keys_before: 122, keys_after: 120 },
    })
    setup(call)
    fireEvent.click(await screen.findByRole('button', { name: 'Open file' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Remove API_URL' }))
    fireEvent.click(screen.getByRole('button', { name: 'Save file' }))
    const dialog = await screen.findByRole('alertdialog')
    expect(within(dialog).getByText('Variables: 122 before, 120 after.')).toBeInTheDocument()
    expect(within(dialog).getByText(/deletes 2 variable/)).toBeInTheDocument()
    expect(within(dialog).getByText('DB_HOST, JWT_KEY')).toBeInTheDocument()
    const confirm = within(dialog).getByRole('button', { name: 'Remove variables and save' })
    expect(confirm).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText('Type api to confirm removing these variables'), { target: { value: 'api' } })
    fireEvent.click(confirm)
    expect(await screen.findByText('Environment file saved.')).toBeInTheDocument()
    expect(JSON.parse(call.mock.calls.at(-2)[1].body).confirm_removed).toEqual(['DB_HOST', 'JWT_KEY'])
  })

  it('flags unquoted shell characters in edited values and duplicate names', async () => {
    setup(backend({
      'POST /admin/apps/api/env/preview': { ...PREVIEW, unquoted: ['DB_PASSWORD', 'OLD_ONE'], duplicates: ['API_URL'] },
    }))
    fireEvent.click(await screen.findByRole('button', { name: 'Open file' }))
    fireEvent.change(await screen.findByLabelText('Value of DB_PASSWORD'), { target: { value: 'a;b' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save file' }))
    const dialog = await screen.findByRole('alertdialog')
    expect(within(dialog).getByText(/Unquoted values.*: DB_PASSWORD\. If the app/)).toBeInTheDocument()
    expect(within(dialog).getByText(/1 other unquoted value/)).toBeInTheDocument()
    expect(within(dialog).getByText(/Defined more than once: API_URL/)).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Save file' })).toBeEnabled()
  })

  it('does not open the dialog when the preview is refused, and says what restart does', async () => {
    setup(backend({ 'POST /admin/apps/api/env/preview': new Error('line 2 is not KEY=value or a # comment') }))
    fireEvent.click(await screen.findByRole('button', { name: 'Open file' }))
    expect(await screen.findByText(/the previous file is put back/)).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Value of DB_PASSWORD'), { target: { value: 'x' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save file' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('line 2 is not KEY=value')
    expect(screen.queryByRole('alertdialog')).not.toBeInTheDocument()
  })

  it('offers restore only when an earlier version exists and asks for the app name', async () => {
    const call = backend({ 'GET /admin/apps/api/env': { ...STATUS, backups: 2 } })
    setup(call)
    fireEvent.click(await screen.findByRole('button', { name: 'Restore previous version' }))
    const dialog = screen.getByRole('alertdialog')
    const confirm = within(dialog).getByRole('button', { name: 'Restore previous version' })
    expect(confirm).toBeDisabled()
    fireEvent.change(within(dialog).getByLabelText('Type api to confirm'), { target: { value: 'api' } })
    fireEvent.click(confirm)
    expect(await screen.findByText('Previous version restored.')).toBeInTheDocument()
    expect(call).toHaveBeenCalledWith('/admin/apps/api/env/restore', {
      method: 'POST', body: JSON.stringify({ confirm: 'api', restart: true }),
    })
  })

  it('explains a disabled helper or a missing file instead of offering to open it', async () => {
    setup(backend({ 'GET /admin/apps/api/env': { path: '/etc/api/api.env', available: false } }))
    expect(await screen.findByText(/not enabled on this server/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Open file' })).not.toBeInTheDocument()
  })

  it('reports a missing file', async () => {
    setup(backend({ 'GET /admin/apps/api/env': { path: '/etc/api/api.env', available: true, exists: false } }))
    expect(await screen.findByText(/does not exist on the server/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Open file' })).not.toBeInTheDocument()
  })
})
