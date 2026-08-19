import { WEBUI_API_BASE_URL } from '$lib/constants';

export type GlmUsageWindow = {
	label: string;
	unit: number;
	percentage: number;
	used: number;
	total: number;
	remaining: number;
	resets_at: number | null;
};

export type GlmUsage = {
	plan: string | null;
	windows: GlmUsageWindow[];
	fetched_at: number;
};

export const getGlmUsage = async (token: string, refresh = false): Promise<GlmUsage | null> => {
	let error: string | null = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/glm/usage${refresh ? '?refresh=true' : ''}`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.error('glm usage fetch failed', err);
			error = err?.detail ?? 'request failed';
			return null;
		});

	if (error) {
		throw error;
	}

	return res as GlmUsage;
};
