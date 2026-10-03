/** Copy for Quick Ask: the floating prompt, its hand-off, and its settings. */
export const quickAsk = {
  // The panel
  'quickAsk.field': 'Ask AgentOS',
  'quickAsk.placeholder': 'Ask AgentOS anything…',
  'quickAsk.hint.new': 'new chat',
  'quickAsk.hint.current': 'current chat',
  'quickAsk.hint.close': 'close',
  'quickAsk.tooLong': 'Too long for Quick Ask (20 kB at most). Paste it into a chat instead.',
  'quickAsk.refused': 'That did not go through. Press Return to try again.',

  // The main window
  'quickAsk.waiting': 'Quick Ask will send this once the gateway is running:',

  // Settings › Behaviour, Settings › Shortcuts
  'settings.behaviour.quickAsk': 'Quick Ask',
  'settings.behaviour.quickAsk.blurb': 'A prompt over any app, one keystroke away.',
  'settings.behaviour.quickAsk.enabled': 'Global shortcut',
  'settings.behaviour.quickAsk.enabled.help':
    'Return asks in a new chat; Option-Return adds to the chat you were in.',
  'settings.behaviour.quickAsk.shortcut': 'Shortcut',
  'settings.behaviour.quickAsk.shortcut.help': 'Works from any app while AgentOS is running.',
  'settings.behaviour.quickAsk.unavailable':
    'Unavailable: another app or macOS already uses this shortcut. Choose another one.',
  'settings.shortcuts.quickAsk': 'Quick Ask, from any app',
} as const
