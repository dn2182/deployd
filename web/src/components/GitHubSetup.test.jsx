import { fireEvent, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import GitHubSetup from './GitHubSetup.jsx'
import SetupSummary from './SetupSummary.jsx'
import { renderWith } from '../test-utils.jsx'

const spec = { github_repository: 'acme/site', deploy_url: 'https://deployd.example.com' }

afterEach(() => vi.restoreAllMocks())

function panel(settings = {}) {
  const call = vi.fn().mockResolvedValue({ filename: 'site-github-actions.zip', content_base64: btoa('zip') })
  const onChanged = vi.fn()
  renderWith(<GitHubSetup name="site" spec={{ ...spec, ...settings }} call={call} onChanged={onChanged} />)
  return { call, onChanged }
}

describe('GitHub setup', () => {
  it('defaults to a manual pnpm workflow and downloads the generated bundle', async () => {
    const createUrl = vi.fn().mockReturnValue('blob:setup')
    Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: createUrl })
    Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const { call, onChanged } = panel()
    expect(screen.getByLabelText('Also deploy automatically on pushes to this branch')).not.toBeChecked()
    fireEvent.change(screen.getByLabelText('Project folder'), { target: { value: 'frontend' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save settings and download ZIP' }))
    await waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1))
    expect(call.mock.calls[0][0]).toBe('/admin/apps/site/github-actions')
    expect(JSON.parse(call.mock.calls[0][1].body)).toMatchObject({
      kind: 'pnpm', project_dir: 'frontend', build_command: 'pnpm run build', output_dir: 'dist', automatic: false,
    })
    expect(createUrl).toHaveBeenCalledWith(expect.any(Blob))
    expect(click).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('status')).toHaveTextContent('Commit the generated files')
    expect(screen.getByRole('link', { name: 'Open GitHub Actions variables' })).toHaveAttribute('href', 'https://github.com/acme/site/settings/variables/actions')
  })

  it('uses only a source folder for plain HTML', () => {
    panel()
    fireEvent.change(screen.getByLabelText('Build preset'), { target: { value: 'static' } })
    expect(screen.getByLabelText('Source folder')).toHaveValue('.')
    expect(screen.queryByLabelText('Build command')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Build output folder')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Node version')).not.toBeInTheDocument()
  })

  it('restores saved settings and clearly warns about push deployment', () => {
    panel({ github_actions: { kind: 'npm', project_dir: 'web', build_command: 'npm run build', branch: 'production', automatic: true } })
    expect(screen.getByLabelText('Build preset')).toHaveValue('npm')
    expect(screen.getByLabelText('Project folder')).toHaveValue('web')
    expect(screen.getByLabelText('Deployment branch')).toHaveValue('production')
    expect(screen.getByLabelText('Also deploy automatically on pushes to this branch')).toBeChecked()
    expect(screen.getByText(/Committing this workflow can deploy immediately/)).toBeInTheDocument()
  })

  it('switches Node commands but preserves custom build commands', () => {
    panel()
    fireEvent.change(screen.getByLabelText('Build preset'), { target: { value: 'npm' } })
    expect(screen.getByLabelText('Build command')).toHaveValue('npm run build')
    fireEvent.change(screen.getByLabelText('Build command'), { target: { value: 'make release' } })
    fireEvent.change(screen.getByLabelText('Build preset'), { target: { value: 'custom' } })
    expect(screen.getByLabelText('Build command')).toHaveValue('make release')
    expect(screen.getByText(/Include any toolchain setup/)).toBeInTheDocument()
  })

  it('shows failures without claiming a successful download', async () => {
    const { call } = panel()
    call.mockRejectedValueOnce(new Error('set the GitHub repository first'))
    fireEvent.click(screen.getByRole('button', { name: 'Save settings and download ZIP' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('set the GitHub repository first')
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save settings and download ZIP' })).not.toBeDisabled()
  })

  it('appears immediately after application setup with Spanish instructions', () => {
    renderWith(<SetupSummary result={{ app: 'site' }} spec={spec} call={vi.fn()} onDismiss={vi.fn()} />, { language: 'es' })
    expect(screen.getByRole('region', { name: 'Configuración de GitHub Actions' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Guardar ajustes y descargar ZIP' })).toBeInTheDocument()
    expect(screen.getByText(/ZIP no contiene valores secretos/)).toBeInTheDocument()
  })
})
