import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { createPortal } from 'react-dom'
import { describe, expect, it, vi } from 'vitest'
import { Composer } from './Composer'

// Issue #3548: the console's composer has a Run modes popover (execution mode,
// Pilot Router, plan mode, session usage); the desktop composer had no slot
// for it, so none of those controls were reachable from the app.

function Body({ withConfirm = false }: { withConfirm?: boolean }) {
  return (
    <div>
      <span>run-modes-body</span>
      {withConfirm
        ? createPortal(
            <div role="alertdialog" aria-label="Confirm bypass">
              <button type="button">Enable bypass</button>
            </div>,
            document.body,
          )
        : null}
    </div>
  )
}

function mount(props: Partial<React.ComponentProps<typeof Composer>> = {}) {
  return render(<Composer onSend={vi.fn()} busy={false} toolbar={<Body />} {...props} />)
}

const trigger = () => screen.getByRole('button', { name: 'Run modes' })

describe('Composer · Run modes', () => {
  it('has no Run modes button when the view passes no toolbar', () => {
    render(<Composer onSend={vi.fn()} busy={false} />)
    expect(screen.queryByRole('button', { name: 'Run modes' })).toBeNull()
  })

  it('mounts the toolbar body only while the popover is open', async () => {
    mount()
    expect(trigger()).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('run-modes-body')).toBeNull()

    fireEvent.click(trigger())

    const dialog = screen.getByRole('dialog', { name: 'Run modes' })
    expect(dialog).toHaveTextContent('run-modes-body')
    expect(trigger()).toHaveAttribute('aria-expanded', 'true')
    expect(trigger()).toHaveAttribute('aria-controls', dialog.id)
    expect(screen.getByRole('button', { name: 'Close run modes' })).toHaveFocus()

    fireEvent.click(trigger())
    await waitFor(() => expect(screen.queryByText('run-modes-body')).toBeNull())
  })

  it('Escape closes the popover and returns focus, without clearing the draft', async () => {
    const onAbort = vi.fn()
    mount({ onAbort })
    const textarea = screen.getByRole('textbox')
    fireEvent.change(textarea, { target: { value: 'keep me' } })
    fireEvent.click(trigger())

    fireEvent.keyDown(screen.getByRole('button', { name: 'Close run modes' }), { key: 'Escape' })

    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(trigger()).toHaveFocus()
    expect(textarea).toHaveValue('keep me')
    expect(onAbort).not.toHaveBeenCalled()
  })

  it('the close button and an outside press both close it', async () => {
    mount()
    fireEvent.click(trigger())
    fireEvent.click(screen.getByRole('button', { name: 'Close run modes' }))
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
    expect(trigger()).toHaveFocus()

    fireEvent.click(trigger())
    fireEvent.mouseDown(document.body)
    await waitFor(() => expect(screen.queryByRole('dialog')).toBeNull())
  })

  it('a press or Escape inside the portalled bypass confirm keeps the popover open', () => {
    // The confirm lives in <body>, outside the popover's DOM. Closing the
    // popover on that press would unmount the confirm before its click lands.
    mount({ toolbar: <Body withConfirm /> })
    fireEvent.click(trigger())
    const confirm = screen.getByRole('button', { name: 'Enable bypass' })

    fireEvent.mouseDown(confirm)
    fireEvent.keyDown(confirm, { key: 'Escape' })

    // aria-expanded is the open state itself; the dialog node alone would
    // linger through the exit animation and hide a close.
    expect(trigger()).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('button', { name: 'Enable bypass' })).toBeInTheDocument()
  })
})
