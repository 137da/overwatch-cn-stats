// 使用 Playwright 页面运行；需先通过 addInitScript 注入同目录的 frontend_fixture.js。
async (page, { baseUrl = 'http://127.0.0.1:18765', artifactDir = 'build' } = {}) => {
  const passed = [];
  const errors = [];
  const check = (value, message) => { if (!value) throw Error(message); passed.push(message); };
  const calls = (name) => page.evaluate(name => window.__fixture.calls.filter(c => c.name === name), name);
  const query = async (tag = '晚风#1024') => {
    await page.locator('#tag').fill(tag);
    await page.locator('#search-form').evaluate(form => form.requestSubmit());
    await page.waitForFunction(() => !document.querySelector('#btn-search').disabled);
  };
  const screenshot = async name => page.screenshot({ path: `${artifactDir}/ui_${name}.png` });
  page.on('pageerror', error => errors.push(error.message));
  await page.setViewportSize({ width: 1100, height: 780 });
  await page.goto(`${baseUrl}/index.html`);
  await page.locator('#account-summary').filter({ hasText: '已就绪' }).waitFor();
  check(await page.locator('#empty').isVisible(), '初始空状态可见');
  check((await calls('get_player')).length === 0, '启动不自动查询玩家');
  await screenshot('empty');
  await query();
  check(await page.locator('#avg .stat').count() === 6, '六项原有指标完整保留');
  check(await page.locator('#matches button.match').count() === 6, '对局使用可键盘操作的按钮');
  const rankColors = await page.locator('#ranks .rank').evaluateAll(cards => cards.map(c => getComputedStyle(c).backgroundColor));
  check(rankColors[1] === rankColors[2], '相同大师段位使用相同颜色');
  check(rankColors[0] !== rankColors[1], '钻石与大师色面不同');
  check(await page.locator('#ranks .rank').last().innerText().then(t => t.includes('未定级')), '无段位职责显示未定级');
  await screenshot('main');
  const beforeStats = (await calls('get_player')).length;
  await page.locator('#stat-mode [data-stat="quick"]').click();
  const quickColors = await page.locator('#ranks .rank').evaluateAll(cards => cards.map(c => getComputedStyle(c).backgroundColor));
  check(quickColors.every(c => c === rankColors[3]), '快速职责全部使用统一的无段位灰色');
  check((await calls('get_player')).length === beforeStats, '统计模式切换不发请求');
  await page.locator('#stat-mode [data-stat="sport"]').click();
  await page.locator('#mode-filter [data-mode="quick"]').click();
  await page.waitForFunction(() => !document.querySelector('#btn-search').disabled);
  let playerCalls = await calls('get_player');
  check(playerCalls.at(-1).args[0].stats === false && playerCalls.at(-1).args[0].mode === 'quick', '对局筛选沿用 stats=false 省请求路径');
  await page.locator('#btn-more').click();
  await page.locator('#btn-more').waitFor({ state: 'hidden' });
  playerCalls = await calls('get_player');
  check(playerCalls.at(-1).args[0].limit === 1 && playerCalls.at(-1).args[0].stats === false, '分页只拉取一页且不重新查询统计');
  await page.locator('#season').selectOption('27');
  await page.waitForFunction(() => !document.querySelector('#btn-search').disabled);
  playerCalls = await calls('get_player');
  check(playerCalls.at(-1).args[0].stats === true && playerCalls.at(-1).args[0].season === '27', '赛季切换刷新统计与对局');

  await page.locator('#btn-library').click();
  check(await page.locator('#library-modal').isVisible(), '玩家库面板正常打开');
  check(await page.locator('#library-filter').evaluate(e => e === document.activeElement), '玩家库自动聚焦检索框');
  await page.locator('#library-tabs [data-tab="all"]').click();
  check(await page.locator('#library-list .lib-item').count() === 3, '玩家库全部列表正常显示');
  await page.locator('#library-filter').fill('一只');
  await page.waitForFunction(() => document.querySelectorAll('#library-list .lib-item').length === 1);
  await page.locator('#library-filter').fill('');
  await page.waitForTimeout(350);
  await screenshot('library');
  await page.keyboard.press('Escape');
  check(await page.locator('#btn-library').evaluate(e => e === document.activeElement), '关闭面板恢复导航焦点');

  await page.locator('#btn-accounts').click();
  await page.locator('#accounts .account').first().waitFor();
  await page.locator('#accounts .account').first().locator('summary.head').click();
  await page.locator('[data-switch="query"]').click();
  await page.waitForFunction(() => window.__fixture.calls.some(c => c.name === 'switch_account'));
  check(await page.locator('#accounts .account').first().evaluate(e => e.open), '账号刷新保持展开状态');
  await screenshot('accounts');
  await page.keyboard.press('Escape');

  const reportBefore = (await calls('report_check')).length;
  await page.locator('#btn-reports').click();
  check((await calls('report_check')).length === reportBefore, '打开举报面板不额外检查接口');
  await page.locator('#report-toggle').check();
  await page.locator('#report-panel .report-item').waitFor();
  check(await page.locator('#report-summary').innerText().then(t => t.includes('已开启')), '开启提醒同步侧栏状态');
  await screenshot('reports');
  await page.locator('#report-toggle').uncheck();
  check(await page.locator('#report-summary').innerText().then(t => t.includes('未开启')), '关闭提醒同步侧栏状态');
  await page.keyboard.press('Escape');

  await page.locator('#btn-settings').click();
  check(await page.locator('#set-cli-limit').inputValue() === '3000', '设置显示当前 CLI 每日请求上限');
  for (const value of ['-1', '5001', '1.5', '']) {
    await page.locator('#set-cli-limit').fill(value);
    const beforeSave = (await calls('save_settings')).length;
    await page.locator('#settings-form button[type="submit"]').click();
    check((await calls('save_settings')).length === beforeSave && await page.locator('#settings-modal').isVisible(), `CLI 上限 ${value || '空值'} 被表单阻止提交`);
  }
  await page.locator('#set-cli-limit').fill('1200');
  await page.locator('#set-heartbeat').fill('90');
  await page.locator('#settings-close').focus();
  await page.keyboard.press('Shift+Tab');
  check(await page.evaluate(() => !!document.activeElement.closest('#settings-modal')), '键盘焦点限制在当前弹窗内');
  await screenshot('settings');
  await page.locator('#settings-form button[type="submit"]').click();
  await page.locator('#settings-modal').waitFor({ state: 'hidden' });
  check((await calls('save_settings')).at(-1).args[0].report_heartbeat_sec === 90, '设置保存沿用原接口');
  check((await calls('save_settings')).at(-1).args[0].cli_daily_limit === 1200, '保存设置包含 CLI 每日请求上限');
  await page.locator('#btn-settings').click();
  check(await page.locator('#set-cli-limit').inputValue() === '1200', '重新打开设置恢复已保存的 CLI 上限');
  await page.locator('#set-cli-limit').fill('0');
  await page.locator('#settings-form button[type="submit"]').click();
  await page.locator('#settings-modal').waitFor({ state: 'hidden' });
  check((await calls('save_settings')).at(-1).args[0].cli_daily_limit === 0, 'CLI 不限额的零值完整保存');
  await page.locator('#btn-settings').click();
  check(await page.locator('#set-cli-limit').inputValue() === '0', '重新打开设置保留 CLI 不限额零值');
  await page.locator('#set-cli-limit').fill('5000');
  await page.locator('#settings-form button[type="submit"]').click();
  await page.locator('#settings-modal').waitFor({ state: 'hidden' });
  check((await calls('save_settings')).at(-1).args[0].cli_daily_limit === 5000, 'CLI 上限边界 5000 可以保存');

  await page.locator('#btn-find').click();
  await page.locator('#find-go').click();
  await page.locator('#find-list .match').first().waitFor();
  await screenshot('find');
  await page.locator('#find-list .match').first().click();
  await page.locator('#detail .sb-player').first().waitFor();
  const scoreboard = page.locator('#detail .sb-player').first();
  await scoreboard.focus();
  await page.keyboard.press('Enter');
  check(await scoreboard.getAttribute('aria-expanded') === 'true', '记分板支持键盘展开玩家数据');
  check(await page.locator('#detail .teams .team').count() === 2, '完整保留两队记分板');
  check(await page.locator('#detail .hero-card').count() === 1, '完整保留英雄统计');
  await screenshot('detail');
  await page.keyboard.press('Escape');
  check(await page.locator('#modal').isHidden() && await page.locator('#find-modal').isVisible(), 'Escape 只关闭顶层详情，保留查找结果');
  await page.keyboard.press('Escape');
  check(await page.locator('#view-main').evaluate(e => !e.inert), '全部面板关闭后恢复主界面交互');

  for (const width of [1440, 1100, 820, 360]) {
    await page.setViewportSize({ width, height: width === 820 ? 600 : 780 });
    const layout = await page.evaluate(() => ({ width: innerWidth, body: document.body.scrollWidth, content: document.querySelector('.content').clientWidth, scroll: document.querySelector('.content').scrollWidth }));
    check(layout.body <= width && layout.scroll <= layout.content + 1, `${width}px 窗口没有整体横向溢出`);
    if (width === 820) {
      await screenshot('main_820');
      await page.locator('#btn-settings').click();
      check(await page.locator('#settings-modal .modal-body').evaluate(e => e.scrollHeight <= e.clientHeight + 1 || getComputedStyle(e).overflowY === 'auto'), '最小高度下设置面板可滚动');
      await screenshot('settings_820');
      await page.keyboard.press('Escape');
    }
  }
  await page.setViewportSize({ width: 1100, height: 780 });
  for (const code of ['not_found', 'banned', 'rate_limited', 'reject']) {
    await page.evaluate(code => { window.__fixture.error = code; }, code);
    await query();
    check(await page.locator('#notice').isVisible(), `${code} 错误保持可见`);
    check(await page.locator('#btn-search').isEnabled(), `${code} 错误后恢复查询按钮`);
    if (code === 'banned' || code === 'rate_limited') check(await page.locator('[data-notice-retry]').count() === 0, `${code} 不提供自动重试入口`);
  }
  await screenshot('network_error');
  await page.evaluate(() => { window.__fixture.error = null; });
  await page.locator('[data-notice-retry]').click();
  await page.locator('#profile .name').waitFor();
  await page.waitForFunction(() => !document.querySelector('#btn-search').disabled);

  await page.evaluate(() => { window.__fixture.delay = 350; });
  const beforeSlow = (await calls('get_player')).length;
  await page.locator('#search-form').evaluate(form => { form.requestSubmit(); form.requestSubmit(); });
  await page.waitForFunction(() => !document.querySelector('#btn-search').disabled);
  check((await calls('get_player')).length === beforeSlow + 1, '连续查询只发出一个请求');
  await page.evaluate(() => { window.__fixture.delay = 0; });

  await page.locator('#btn-accounts').click();
  if (!await page.locator('#accounts .account').first().evaluate(e => e.open)) await page.locator('#accounts .account').first().locator('summary.head').click();
  await page.locator('[data-login="query"]').click();
  await page.locator('#qr-wrap[data-state="ready"]').waitFor();
  check(await page.locator('.modal:not(.hidden)').count() === 0, '扫码时关闭账号面板，二维码不会被遮挡');
  await screenshot('login');
  await page.evaluate(() => { window.__fixture.qrState = 'expired'; });
  await page.locator('#qr-wrap[data-state="expired"]').waitFor();
  check(await page.locator('#qr-hint-text').innerText() === '二维码已过期', '二维码过期状态明确可重试');
  await page.locator('#btn-login-back').click();
  check(await page.locator('#view-main').isVisible(), '扫码返回后正常回到工作台');
  const ids = await page.evaluate(() => Array.from(document.querySelectorAll('[id]')).map(e => e.id));
  check(new Set(ids).size === ids.length, '全页没有重复 ID');
  check(errors.length === 0, '所有回归操作无浏览器脚本异常：' + errors.join('; '));
  await query();
  await page.locator('.content').evaluate(e => e.scrollTop = 0);
  await page.locator('#tag').blur();
  await screenshot('final');
  return { checks: passed.length, passed, errors, playerRequests: (await calls('get_player')).length };
}
