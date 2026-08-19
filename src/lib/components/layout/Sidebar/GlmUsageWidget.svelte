<script lang="ts">
	import { onMount, onDestroy } from 'svelte';
	import { fade } from 'svelte/transition';
	import { toast } from 'svelte-sonner';
	import { getGlmUsage, type GlmUsage } from '$lib/apis/glm';
	import { token } from '$lib/stores';

	export let className = '';

	let usage: GlmUsage | null = null;
	let loading = false;
	let error: string | null = null;
	let now = Date.now();
	let timer: ReturnType<typeof setInterval>;
	let tickTimer: ReturnType<typeof setInterval>;

	const REFRESH_MS = 5 * 60 * 1000; // poll every 5 minutes

	async function load(refresh = false) {
		if (loading) return;
		loading = true;
		error = null;
		try {
			usage = await getGlmUsage($token, refresh);
		} catch (e) {
			error = typeof e === 'string' ? e : 'failed to load usage';
		} finally {
			loading = false;
		}
	}

	function fmtReset(resetsAt: number | null): string {
		if (!resetsAt) return '—';
		const ms = resetsAt * 1000 - now;
		if (ms <= 0) return 'resetting…';
		const h = Math.floor(ms / 3_600_000);
		const m = Math.floor((ms % 3_600_000) / 60_000);
		return h > 0 ? `${h}h ${m}m` : `${m}m`;
	}

	function barClass(pct: number | null | undefined): string {
		const p = pct ?? 0;
		if (p >= 90) return 'bg-red-500';
		if (p >= 70) return 'bg-amber-500';
		return 'bg-emerald-500';
	}

	onMount(() => {
		load();
		timer = setInterval(() => load(), REFRESH_MS);
		tickTimer = setInterval(() => (now = Date.now()), 60_000);
	});

	onDestroy(() => {
		clearInterval(timer);
		clearInterval(tickTimer);
	});
</script>

<div class="{className} px-2 py-1.5" transition:fade={{ duration: 150 }}>
	{#if error}
		<button
			class="w-full rounded-lg px-2 py-1.5 text-left text-xs text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-850"
			on:click={() => load(true)}
			title={error}
		>
			⚠️ GLM usage unavailable — tap to retry
		</button>
	{:else if !usage}
		<div class="px-2 py-1.5 text-xs text-gray-400">
			{#if loading}<span class="animate-pulse">loading GLM usage…</span>{:else}—{/if}
		</div>
	{:else}
		<div class="rounded-lg px-2 py-1.5 dark:text-gray-200">
			<div class="mb-1 flex items-center justify-between text-[11px] font-medium text-gray-500 dark:text-gray-400">
				<span>GLM {usage.plan ?? ''}</span>
				<button
					class="hover:text-gray-800 dark:hover:text-gray-200"
					on:click={() => load(true)}
					title="refresh now"
					disabled={loading}
				>
					{#if loading}⟳{:else}↻{/if}
				</button>
		</div>
			{#each usage.windows as w (w.label)}
				<div class="mb-1.5 last:mb-0">
					<div class="flex items-baseline justify-between text-[11px] text-gray-500 dark:text-gray-400">
						<span>{w.label}</span>
						<span>{w.used}/{w.total} · resets in {fmtReset(w.resets_at)}</span>
					</div>
					<div class="mt-0.5 h-1.5 w-full overflow-hidden rounded-full bg-gray-200 dark:bg-gray-800">
						<div
							class="h-full rounded-full transition-all duration-500 {barClass(w.percentage)}"
							style="width: {Math.min(100, Math.max(2, w.percentage ?? 0))}%"
						></div>
					</div>
				</div>
			{/each}
		</div>
	{/if}
</div>
