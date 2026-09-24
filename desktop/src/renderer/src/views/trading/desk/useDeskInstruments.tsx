import { KeyRound } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router'
import { toast } from 'sonner'
import { useRpc } from '@/app/providers'
import type { RawJob } from '@/views/cron/logic'
import { Button } from '~/components/ui/button'
import { sessionPath } from '~/components/sidebar/SessionRow'
import { t } from '~/i18n'
import { useNow } from '~/lib/use-now'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  invalidateTrading,
  useBatchLegs,
  useOrderDecision,
  useOrders,
  useTradingStatus,
} from '~/stores/trading'
import { useTradingUi, type BookTab } from '~/stores/trading-ui'
import { useUi } from '~/stores/ui'
import { Notice } from '~/views/settings/parts'
import { errorText, isAwaitingApproval, sameAddress } from '../logic'
import { useSwitchProvider } from '../useSwitchProvider'
import { WalletSheet, type WalletSheetMode } from '../WalletSheet'
import type { Limits, Order, ProviderId, Wallet } from '../types'
import { ApprovalsRegion } from './ApprovalsRegion'
import { ComposerSeats } from './ComposerSeats'
import {
  alreadyDecided,
  batchIdsOf,
  composerPlaceholder,
  missionStatus,
  orderKindWord,
  orderLine,
  ordersForSession,
  rejectionMessage,
  withBatchLegs,
  type MissionForm,
  type MissionKind,
} from './desk-logic'
import { MissionContract } from './MissionContract'
import { MissionControls, MissionStrip, missionWord } from './MissionControls'
import { MissionPicker } from './MissionPicker'
import type { MissionsApi } from './missions'
import type { MissionPreset } from './presets'
import { BurnSheet } from './BurnSheet'
import { SendSheet } from './SendSheet'
import { AllowancesSheet, NetworkSheet } from './ToolSheets'
import { ToolsPicker } from './ToolsPicker'
import { DecodeSheet } from '../DecodeSheet'

const ROTATE_MS = 6000
const NO_JOBS: RawJob[] = []
const NO_RUNS: ReadonlySet<string> = new Set()
/** A settled ask stays in the region this long as a stamp. */
const STAMP_TTL_MS = 10 * 60_000

export interface DeskGate {
  needsKey: boolean
  provider: ProviderId
}

/** What the desk hands the chat when the open session is the desk's. */
export interface DeskProps {
  /** The power-on is playing: the chat snaps its own layout instead of
      springing, so Motion's hero exit and dock move do not fight the
      desk's choreography. */
  entering: boolean
  wallets: Wallet[]
  primary: string | null
  limits: Limits | null
  gate: DeskGate
  /** The desk's missions, bound once by the frame (the strip reads them too). */
  missions: MissionsApi
  /** After the first send: files the session into the desk project. */
  onFirstSend: () => void
  /** Start over in a fresh desk chat. */
  onStartFresh: () => void
  onOpenBookTab: (tab: BookTab) => void
  onStreaming: (busy: boolean) => void
  onSessionPending: (count: number) => void
  /**
   * The chat's reject path — the one that tells the agent why — offered to
   * the frame so the BOOK's Orders tab rejects the same way. Null on unmount.
   */
  onBindReject: (reject: ((order: Order, reason: string) => void) | null) => void
}

export interface DeskInstruments {
  /** Between the transcript and the composer: the agent's asks. */
  region: ReactNode
  /** Above the composer: gate notices, the mission strip and controls. */
  dockAbove: ReactNode
  seats: ReactNode
  placeholder: string | undefined
  /** An ask is pending: the desk goes still. */
  still: boolean
  modal: ReactNode
  /** The composer is idle and the thread is empty: the desk's invitation. */
  emptyHint: ReactNode
  onFocusChange: (focused: boolean) => void
}

/**
 * The desk's instruments around the shared chat: everything TradingChat used
 * to own, as slots the ChatView renders in place. Runs with `desk === null`
 * for an ordinary chat (queries disabled, nothing rendered) so the hook order
 * never changes when the mode does — that is what keeps the composer the
 * same node across the switch.
 */
export function useDeskInstruments(
  desk: DeskProps | null,
  ctx: {
    sessionKey: string
    /** Send text straight into the session (no composer round-trip). A no-op while a turn streams. */
    sendText: (text: string) => void
    /** Queue text for the next turn, leaving the composer's draft alone. */
    queueText: (text: string) => void
    /** Submit through the composer's rules (queues while a turn runs). */
    submitText: (text: string) => void
    busy: boolean
    composerValue: string
    idle: boolean
    hasMessages: boolean
    focusOrderId: string | null
    setFocusOrderId: (id: string | null) => void
  },
): DeskInstruments {
  const rpc = useRpc()
  const queryClient = useQueryClient()
  const enabled = desk !== null
  const tradingStatus = useTradingStatus(enabled)
  const switchProvider = useSwitchProvider()
  const navigate = useNavigate()
  const location = useLocation()
  const openSettings = useUi((s) => s.openSettings)
  const {
    sessionKey,
    sendText,
    queueText,
    submitText,
    busy,
    composerValue,
    idle,
    hasMessages,
    focusOrderId,
  } = ctx
  const { setFocusOrderId } = ctx

  useEffect(() => {
    if (desk) desk.onStreaming(busy)
  }, [busy, desk])
  // The mutation callbacks below read the flag at completion time, not at
  // the render that started them.
  const busyRef = useRef(busy)
  useEffect(() => {
    busyRef.current = busy
  }, [busy])
  // A message the agent must read: straight in when the session is idle,
  // queued for the next turn while one streams (a direct send is dropped
  // then — a rejection reason vanished that way).
  const postToAgent = useCallback(
    (text: string) => (busyRef.current ? queueText(text) : sendText(text)),
    [queueText, sendText],
  )

  // ── Approvals for this session ──────────────────────────────────────────
  // Asks come from the awaiting set itself, not from the newest-N page: a
  // page can silently omit an older ask. Stamps (settled receipts) are read
  // from the recent page, where the outcome lands.
  const awaiting = useOrders('awaiting_approval', enabled, 100)
  const recent = useOrders(undefined, enabled, 100)
  const sessionAwaiting = useMemo(
    () => ordersForSession(awaiting.orders, sessionKey).filter(isAwaitingApproval),
    [awaiting.orders, sessionKey],
  )
  const batchIds = useMemo(() => batchIdsOf(sessionAwaiting), [sessionAwaiting])
  const batches = useBatchLegs(batchIds, enabled)
  const pendingOrders = useMemo(
    () => withBatchLegs(sessionAwaiting, batches),
    [sessionAwaiting, batches],
  )
  const sessionOrders = useMemo(
    () => ordersForSession(recent.orders, sessionKey),
    [recent.orders, sessionKey],
  )
  useEffect(() => {
    if (desk) desk.onSessionPending(pendingOrders.length)
  }, [pendingOrders.length, desk])
  const [mountedAt] = useState(() => Date.now())
  const now = useNow(30_000)
  // Stamps you have read: closed by hand, they never come back this session.
  const [dismissed, setDismissed] = useState<ReadonlySet<string>>(() => new Set())
  const onDismissStamp = useCallback(
    (orderId: string) => setDismissed((prev) => new Set(prev).add(orderId)),
    [],
  )
  const settled = useMemo(
    () =>
      sessionOrders.filter(
        (o) =>
          !isAwaitingApproval(o) &&
          o.initiator === 'agent' &&
          o.createdAt >= mountedAt &&
          now - o.updatedAt < STAMP_TTL_MS &&
          !dismissed.has(o.orderId),
      ),
    [sessionOrders, now, mountedAt, dismissed],
  )
  const decide = useOrderDecision()
  // The toast names what was decided — "Send rejected · 0.00001 ETH →
  // 0x6c83…8312" — never "Swap" for a send. The legs of a batch are read
  // from the region so a multisend is one line.
  const pendingRef = useRef(pendingOrders)
  useEffect(() => {
    pendingRef.current = pendingOrders
  }, [pendingOrders])
  const legsOf = useCallback(
    (order: Order): Order[] =>
      order.batchId ? pendingRef.current.filter((o) => o.batchId === order.batchId) : [order],
    [],
  )
  const decisionToast = useCallback(
    (order: Order, decision: 'approved' | 'rejected') => {
      const legs = legsOf(order)
      const word = t(`trading.card.kind.${orderKindWord(order, legs.length)}`)
      const line = orderLine(order, legs, t('trading.send.count'))
      const verb =
        decision === 'approved'
          ? orderKindWord(order, legs.length) === 'swap'
            ? t('trading.approvals.approved')
            : t('trading.approvals.approved.plain')
          : t('trading.approvals.rejected')
      return `${word} ${verb} · ${line}`
    },
    [legsOf],
  )
  const onApprove = useCallback(
    (order: Order) =>
      decide.mutate(
        { orderId: order.orderId, approve: true },
        {
          onSuccess: () =>
            toast.success(decisionToast(order, 'approved'), { id: `trd-order-${order.orderId}` }),
          onError: (err) => {
            // A second decision on an order already decided (two clicks, a
            // decision from the BOOK, the agent's own) is not a failure.
            const text = errorText(err)
            if (alreadyDecided(text)) {
              toast.info(t('trading.approvals.alreadyDecided'), {
                id: `trd-order-${order.orderId}`,
              })
              return
            }
            toast.error(`${t('trading.approvals.failed')}: ${text}`, {
              id: `trd-order-${order.orderId}`,
            })
          },
        },
      ),
    [decide, decisionToast],
  )
  // A mutation, not a bare call: `isPending` locks the card, so a second
  // Enter on the reason cannot reject twice and post two chat messages. The
  // ref closes the gap before React has re-rendered with `isPending`.
  const rejectInFlight = useRef(false)
  const reject = useMutation({
    mutationFn: ({ order, reason }: { order: Order; reason: string }) =>
      rpc.call('trading.orders.reject', { orderId: order.orderId, reason: reason || 'user' }),
    onSuccess: (_res, { order, reason }) => {
      toast.success(decisionToast(order, 'rejected'), { id: `trd-order-${order.orderId}` })
      // The agent reads the reason where it asked. Rejecting one leg of a
      // multisend rejects the batch, and the message says so.
      postToAgent(rejectionMessage(order, reason, legsOf(order).length))
    },
    onError: (err, { order }) => {
      const text = errorText(err)
      if (alreadyDecided(text)) {
        toast.info(t('trading.approvals.alreadyDecided'), { id: `trd-order-${order.orderId}` })
        return
      }
      toast.error(`${t('trading.approvals.failed')}: ${text}`, {
        id: `trd-order-${order.orderId}`,
      })
    },
    onSettled: () => {
      rejectInFlight.current = false
      invalidateTrading(queryClient)
    },
  })
  const { mutate: rejectMutate } = reject
  const onReject = useCallback(
    (order: Order, reason: string) => {
      if (rejectInFlight.current) return
      rejectInFlight.current = true
      rejectMutate({ order, reason: reason.trim() })
    },
    [rejectMutate],
  )
  // The BOOK's Orders tab rejects through this same path, so its rejection
  // also tells the agent why, instead of a bare status flip.
  const bindReject = desk?.onBindReject
  useEffect(() => {
    if (!bindReject) return
    bindReject(onReject)
    return () => bindReject(null)
  }, [bindReject, onReject])
  const deciding = decide.isPending
    ? (decide.variables?.orderId ?? null)
    : reject.isPending
      ? (reject.variables?.order.orderId ?? null)
      : null
  // A notification lands on its card; the URL is cleaned so a reload does not repeat it.
  const orderParam = enabled ? new URLSearchParams(location.search).get('order') : null
  const [seenOrderParam, setSeenOrderParam] = useState<string | null>(null)
  if (orderParam && orderParam !== seenOrderParam) {
    setSeenOrderParam(orderParam)
    setFocusOrderId(orderParam)
  }
  // Decided only once the awaiting set has loaded: on the first render it is
  // empty, and every deep link used to open the Orders tab instead of its card.
  const awaitingLoaded = awaiting.isSuccess
  useEffect(() => {
    if (!orderParam || !desk || !awaitingLoaded) return
    if (!pendingOrders.some((o) => o.orderId === orderParam)) desk.onOpenBookTab('orders')
    void navigate(sessionPath(sessionKey), { replace: true })
  }, [orderParam, navigate, desk, pendingOrders, sessionKey, awaitingLoaded])

  // ── Missions ────────────────────────────────────────────────────────────
  // Bound once by the frame and handed down: a second `useMissions` here
  // would listen to `cron.run.finished` twice and update every job twice.
  const missionJobs = desk?.missions.missions ?? NO_JOBS
  const missionRuns = desk?.missions.running ?? NO_RUNS
  // `pick` is the catalogue; `form` is one contract, with the preset it came
  // from (null for a blank contract, an edit, or the one-shot swap chip).
  const [contract, setContract] = useState<
    | { mode: 'pick' }
    | { mode: 'form'; kind: MissionKind; preset: MissionPreset | null; job?: RawJob | null }
    | null
  >(null)
  // The composer's wallet chip is the one wallet affordance that is always on
  // screen in Trading, so it opens the manager rather than nudging a tab.
  const [walletSheet, setWalletSheet] = useState<WalletSheetMode | null>(null)
  // The Send sheet posts into this chat, so the chat owns it; the BOOK's
  // Tools tab and the composer chip both open it through the store.
  const sheet = useTradingUi((s) => s.sheet)
  const openSheet = useTradingUi((s) => s.openSheet)
  const missionLine = useMemo(() => {
    const first = missionJobs[0]
    if (!first) return null
    const s = missionStatus(first, {
      running: Boolean(first.id && missionRuns.has(first.id)),
      pendingApprovals: pendingOrders.length,
    })
    return `${first.name} · ${missionWord(s.state, s.until)}`
  }, [missionJobs, missionRuns, pendingOrders.length])

  // ── Placeholder rotation ────────────────────────────────────────────────
  const [focused, setFocused] = useState(false)
  const [tick, setTick] = useState(0)
  useEffect(() => {
    if (!enabled || focused || composerValue) return
    const id = window.setInterval(() => setTick((n) => n + 1), ROTATE_MS)
    return () => window.clearInterval(id)
  }, [enabled, focused, composerValue])

  if (!desk) {
    return {
      region: null,
      dockAbove: null,
      seats: null,
      placeholder: undefined,
      still: false,
      modal: null,
      emptyHint: null,
      onFocusChange: setFocused,
    }
  }

  const { missions } = desk
  const primaryWallet =
    desk.wallets.find((w) => sameAddress(w.address, desk.primary)) ?? desk.wallets[0] ?? null
  // The chain the inspector opens on: the primary wallet's first, else the
  // engine's first — not a hardcoded Base.
  const inspectChain =
    primaryWallet?.chains?.[0] ?? tradingStatus.data?.chains?.[0]?.chainId ?? 8453
  const placeholder = composerPlaceholder({
    missionWord: missionLine,
    busy,
    tick,
    steering: t('trading.composer.steering'),
  })

  return {
    still: pendingOrders.length > 0,
    placeholder,
    onFocusChange: setFocused,
    region: (
      <ApprovalsRegion
        pending={pendingOrders}
        settled={settled}
        wallets={desk.wallets}
        deciding={deciding}
        onApprove={onApprove}
        onReject={onReject}
        focusOrderId={focusOrderId}
        onDismiss={onDismissStamp}
      />
    ),
    dockAbove: (
      <div className="trd-dock">
        {desk.gate.needsKey ? (
          <Notice
            tone="info"
            action={
              <Button onClick={() => openSettings('trading')} data-testid="chat-add-key">
                <KeyRound className="size-3.5" strokeWidth={1.75} aria-hidden />
                {t('trading.noKey.cta')}
              </Button>
            }
          >
            <b>{t('trading.noKey.title')}</b> {t('trading.noKey.chatBody')}
          </Notice>
        ) : null}
        <MissionStrip
          missions={missions.missions}
          running={missions.running}
          pendingApprovals={pendingOrders.length}
        />
      </div>
    ),
    seats: (
      <div className="trd-seatstack">
        {missions.missions.length ? (
          <MissionControls
            missions={missions.missions}
            running={missions.running}
            pendingApprovals={pendingOrders.length}
            busy={missions.busy}
            onStart={() => setContract({ mode: 'pick' })}
            onEdit={(job) => setContract({ mode: 'form', kind: 'custom', preset: null, job })}
            onRun={missions.runNow}
            onSetEnabled={missions.setEnabled}
            onRemove={missions.remove}
            showStart={false}
          />
        ) : null}
        <ComposerSeats
          limits={desk.limits}
          onStartMission={() => setContract({ mode: 'pick' })}
          provider={desk.gate.provider}
          providers={tradingStatus.data?.providers ?? []}
          switching={switchProvider.switching}
          wallet={primaryWallet}
          typing={composerValue.length > 0}
          onOpenSettings={() => openSettings('trading')}
          onSwitchProvider={switchProvider.switchTo}
          onOpenWallets={() => setWalletSheet({ kind: 'manage' })}
          onQuick={(kind) => setContract({ mode: 'form', kind, preset: null })}
          onSend={() => openSheet('send')}
          onOpenTools={() => openSheet('pick')}
        />
      </div>
    ),
    modal: walletSheet ? (
      <WalletSheet mode={walletSheet} onClose={() => setWalletSheet(null)} />
    ) : sheet === 'pick' ? (
      <ToolsPicker
        wallet={primaryWallet?.address}
        onPick={(tool) => openSheet(tool)}
        onClose={() => openSheet(null)}
      />
    ) : sheet === 'allowances' ? (
      <AllowancesSheet
        wallet={primaryWallet?.address}
        onBack={() => openSheet('pick')}
        onClose={() => openSheet(null)}
      />
    ) : sheet === 'inspect' ? (
      <DecodeSheet chainId={inspectChain} onClose={() => openSheet(null)} />
    ) : sheet === 'network' ? (
      <NetworkSheet onBack={() => openSheet('pick')} onClose={() => openSheet(null)} />
    ) : sheet === 'burn' ? (
      <BurnSheet
        wallets={desk.wallets}
        primary={desk.primary}
        onClose={() => openSheet(null)}
        onAsk={(prompt) => {
          submitText(prompt)
          openSheet(null)
        }}
      />
    ) : sheet === 'send' || sheet === 'multisend' ? (
      <SendSheet
        wallets={desk.wallets}
        primary={desk.primary}
        multi={sheet === 'multisend'}
        onClose={() => openSheet(null)}
        onAsk={(prompt) => {
          submitText(prompt)
          openSheet(null)
        }}
      />
    ) : contract?.mode === 'pick' ? (
      <MissionPicker
        onPick={(preset) => setContract({ mode: 'form', kind: 'custom', preset })}
        onCustom={() => setContract({ mode: 'form', kind: 'custom', preset: null })}
        onClose={() => setContract(null)}
      />
    ) : contract ? (
      <MissionContract
        kind={contract.kind}
        preset={contract.preset}
        job={contract.job ?? null}
        // Only what the catalogue opened can go back to it: an edit and the
        // one-shot swap chip never passed through it.
        onBack={
          contract.job || contract.kind === 'swap' ? undefined : () => setContract({ mode: 'pick' })
        }
        wallets={desk.wallets}
        primary={desk.primary}
        limits={desk.limits}
        onClose={() => setContract(null)}
        onSend={(prompt) => submitText(prompt)}
        onCreate={(form: MissionForm, prompt) => missions.create(form, prompt)}
        onUpdate={(id, form, prompt) =>
          missions.update(id, {
            name: form.name.trim(),
            text: prompt,
            schedule:
              form.interval.kind === 'every'
                ? { kind: 'every', every_seconds: form.interval.seconds }
                : { kind: 'cron', expr: form.interval.expr.trim() },
          })
        }
      />
    ) : null,
    emptyHint:
      !hasMessages && idle ? (
        <div className="trd-chat__empty" data-testid="chat-empty">
          <p className="trd-chat__empty-title">{t('trading.chat.empty.title')}</p>
          <p>{t('trading.chat.empty.body')}</p>
        </div>
      ) : null,
  }
}
