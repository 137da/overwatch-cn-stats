// 仅供浏览器验证的模拟桥接，不读取真实凭据，不访问游戏接口。
(() => {
  const calls = [];
  const ok = (data) => ({ ok: true, data });
  const settings = { report_heartbeat_sec: 60, report_window_min: 30, report_batch_min: 5, cli_daily_limit: 3000 };
  const players = ['晚风#1024', '一只小龙#5201', '橘子汽水#3190'].map((battle_tag) => ({ battle_tag, has_token: true, last_seen: '2026-10-15 00:38:00' }));
  let followed = players.slice(0, 2);
  const recentMatches = ['国王大道', '漓江塔', '伊里奥斯', '中城', '多拉多', '斗兽场'].map((mapName, i) => ({
    matchId: String(i), result: i === 5 ? 'draw' : i % 3 === 2 ? 'loss' : 'win', teamScore: 3, opponentScore: i % 3,
    heroName: i % 2 ? '雾子' : '安娜', roleName: '支援', gameModeName: i % 2 ? '快速' : '排位',
    gameMode: i % 2 ? 'quick' : 'sport', mapName, kill: 24, assist: 18, death: 5, matchTime: '2026-10-15 00:38',
  }));
  const rank = (roleType, roleName, rankName, rankTier) => ({
    roleType, roleName, rankName, rankTier, matchSum: rankName ? 68 : 0, winRate: .583, kda: 3.8,
    rankScore: 4000, season: 'Y2026S2', maxRank: { rankName, rankTier }, maxWinStreak: 5,
    quick: { winRate: .612, matchSum: 37, kda: 4.1 },
  });
  const summary = { winRate: .583, winSum: 126, lossSum: 90, matchSum: 216 };
  const recent = { winRate: .667, roleName: '支援', aveKill: 24, aveDamage: 6240, aveCure: 10284 };
  const playerData = {
    customerToken: 'fake-token', pagination: { hasMore: true }, season: { name: 'Y2026S2' },
    profile: { name: '晚风', title: '守望先锋', level: 426, gameTime: 1286, icon: '' },
    ranks: [rank('tank', '重装', '钻石', 3), rank('dps', '输出', '大师', 5), rank('healer', '支援', '大师', 2), rank('open', '开放', '', '')],
    overview: { sport: { summary, recent, heroes: ['安娜', '雾子', '巴蒂斯特'] }, quick: { summary: { ...summary, winRate: .568 }, recent, heroes: ['雾子'] } },
    recentMatches,
  };
  const teammate = (name, heroName, partyIndex) => ({ name, heroName, rankName: '大师', rankTier: 3, kill: 24, assist: 18, death: 5,
    heroDamage: 6420, cure: 13280, resistDamage: 0, damageTaken: 4810, healingTaken: 3300, finalHit: 8, targetCompetingTime: 25,
    partyIndex, best: ['cure'] });
  const fixture = window.__fixture = { calls, playerData, error: null, delay: 0, loggedIn: true, qrError: false, qrState: 'waiting' };
  const methods = {
    status: () => ok({ loggedIn: fixture.loggedIn }),
    accounts: () => ok([{ label: '查询号', loggedIn: true, nick: '晚风', userName: '本地测试', roleId: 'mock-query', fetchedAt: '刚刚' },
      { label: '主号', loggedIn: false }]),
    saved_accounts: () => ok({ query: [{ roleId: 'mock-query', nick: '晚风', active: true }, { roleId: 'mock-spare', nick: '备用号', active: false }], main: [] }),
    switch_account: () => ok({}), delete_saved: () => ok({}),
    get_cached_players: (query = '', offset = 0) => ok({ followed, all: offset ? [] : players.filter(p => !query || p.battle_tag.includes(query)), hasMore: false }),
    toggle_followed: (tag) => { const exists = followed.some(p => p.battle_tag === tag); followed = exists ? followed.filter(p => p.battle_tag !== tag) : [...followed, players.find(p => p.battle_tag === tag) || { battle_tag: tag }]; return ok({ is_followed: !exists }); },
    seasons: () => ok([{ id: '28', name: 'Y2026S2' }, { id: '27', name: 'Y2026S1' }]),
    get_player: async (params) => {
      if (fixture.delay) await new Promise(resolve => setTimeout(resolve, fixture.delay));
      if (fixture.error === 'reject') throw new Error('模拟桥接断开');
      if (fixture.error) return { ok: false, code: fixture.error, message: '模拟错误，仅用于验证' };
      const data = structuredClone(playerData);
      data.profile.name = params.battleTag.split('#')[0];
      if (params.page > 1) data.pagination.hasMore = false;
      return ok(data);
    },
    get_match_detail: () => ok({ result: 'win', teamScore: 3, opponentScore: 1, mapName: '国王大道', startTime: '2026-10-15 00:38', gameTimeSec: 725,
      teammates: [teammate('晚风#1024', '安娜', 1), teammate('一只小龙#5201', '源氏', 1), teammate('橘子汽水#3190', '雾子', 0)],
      enemies: [teammate('对手#1234', '莱因哈特', 0)], teamBans: ['黑百合'], enemyBans: [],
      heroes: [{ heroName: '安娜', userTimeSec: 600, useTimeRate: .9, statMap: { 治疗量: 13280, 命中率: .68, 睡眠命中: 8 } }] }),
    find_options: () => ok({ heroes: ['安娜', '雾子'], maps: ['国王大道', '漓江塔'] }),
    find_candidates: () => ok({ customerToken: 'fake-token', range: { from: '2026-10-14', to: '2026-10-15' }, seasons: ['Y2026S2'], pagesRead: 1,
      truncated: [], emptySeasons: [], clipped: false, maxDetails: 100, candidates: recentMatches.slice(0, 2) }),
    check_match: () => ok({ hit: true, matched: ['一只小龙'] }),
    get_settings: () => ok({ values: settings, limits: { report_heartbeat_sec: { min: 30, max: 3600 }, report_window_min: { min: 1, max: 1440 }, report_batch_min: { min: 1, max: 120 }, cli_daily_limit: { min: 0, max: 5000 } } }),
    save_settings: (value) => { Object.assign(settings, value); return ok(settings); },
    update_info: () => ok({ frozen: true, version: '2026.10.15-demo', builtAt: '2026-10-15', shell: 1, python: '3.14', rollback: null }),
    install_update: () => ok(null), rollback_update: () => ok({ version: 'previous' }), restart_app: () => ok({}),
    report_check: () => ok({ checkedAt: '刚刚', windowMin: 30, alert: null, players: [{ battleTag: '示例玩家#1001', bnetId: 'mock', reportTime: 'now', minutesAgo: 3, kinds: ['作弊'], queried: false }] }),
    mark_report_queried: () => ok({}), cancel_login: () => ok({}),
    start_login: () => fixture.qrError ? { ok: false, code: 'network', message: '模拟二维码获取失败' } : ok('data:image/svg+xml,' + encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="220" height="220"><rect width="220" height="220" fill="white"/><text x="25" y="110">QR preview only</text></svg>')),
    poll_login: () => ok(fixture.qrState),
  };
  window.pywebview = { api: new Proxy(methods, { get(target, name) { return async (...args) => { calls.push({ name, args }); if (!target[name]) throw Error('未模拟接口: '+name); return target[name](...args); }; } }) };
  window.addEventListener('DOMContentLoaded', () => window.dispatchEvent(new Event('pywebviewready')));
})();
