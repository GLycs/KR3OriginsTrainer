--[[
    Kingdom Rush Origins Trainer Bridge  (KR3 / kr3-desktop-6.4.46)
    ------------------------------------------------------------------
    加载方式：Kingdom Rush Origins.exe -custom_script kr3_bridge
    运行环境：游戏自带的 LuaJIT（Lua 5.1）

    架构：外部 GUI 写 kr3t_state.lua，本脚本在游戏自己的 Lua 运行时里读状态、
          按开关调整运行时数值；心跳超时 / 关闭开关 / 退出时按原值恢复。

    不注入 DLL、不读写进程内存、不修改游戏安装目录中的任何文件。
    只往存档目录写 kr3t_ 前缀的自有文件。
--]]

local BRIDGE_VERSION = "1.0.0"
local STATE_FILE = "kr3t_state.lua"
local STATUS_FILE = "kr3t_status.txt"
local HEARTBEAT_TIMEOUT = 6
local STATUS_INTERVAL = 0.75
local STATE_POLL_INTERVAL = 0.20
local FALLBACK_LAST_LEVEL = 14
local NIL = {}

local ok_signal, signal = pcall(require, "hump.signal")
local ok_storage, storage = pcall(require, "storage")
local ok_gs, GS = pcall(require, "game_settings")

local M = {}
custom_script = M

local config = {
    active = false,
    heartbeat = 0,
    revision = 0,
    -- 速度（默认唯一启用项）
    speed_enabled = true,
    speed = 1,
    paused = false,
    -- 资源
    gold_enabled = false,
    gold_value = 99999,
    gems_enabled = false,
    gems_value = 99999,
    lives_enabled = false,
    lives_value = 20,
    kill_gold_enabled = false,
    kill_gold_multiplier = 2,
    -- 战斗
    invincible = false,
    hero_no_cooldown = false,
    damage_enabled = false,
    damage_multiplier = 3,
    tower_speed_enabled = false,
    tower_speed_multiplier = 3,
    tower_range_enabled = false,
    tower_range_multiplier = 2,
    -- 兵营
    barrack_enabled = false,
    barrack_soldiers = 3,
    barrack_respawn_scale = 1.0,
    -- 进度
    unlock_levels = false,
    three_stars = false,
    -- 只读模式：完全不写游戏存档
    read_only = false,
    -- 一次性命令（用递增 nonce 触发）
    cmd_skip_wave = 0,
    cmd_force_wave = 0,
    cmd_win = 0
}

local poll_acc = 0
local status_acc = 0
local speed_fraction_acc = 0
local last_skip_wave = 0
local last_force_wave = 0
local last_win = 0
local current_store = nil

local speed_owner = nil
local speed_original = nil
local pause_owner = nil
local pause_original = NIL
local gold_owner = nil
local gold_original = NIL
local lives_owner = nil
local lives_original = NIL
local slot_snapshot = nil
local slot_snapshot_idx = nil

local groups = {}

local function weak_group(name)
    if not groups[name] then
        groups[name] = setmetatable({}, {__mode = "k"})
    end
    return groups[name]
end

local function remember_and_set(group_name, obj, key, value)
    if type(obj) ~= "table" or type(obj[key]) ~= "number" then
        return
    end
    local group = weak_group(group_name)
    local fields = group[obj]
    if not fields then
        fields = {}
        group[obj] = fields
    end
    if fields[key] == nil then
        fields[key] = obj[key]
    end
    obj[key] = value
end

local function scale_field(group_name, obj, key, factor)
    if type(obj) ~= "table" or type(obj[key]) ~= "number" then
        return
    end
    local group = weak_group(group_name)
    local fields = group[obj]
    if not fields then
        fields = {}
        group[obj] = fields
    end
    if fields[key] == nil then
        fields[key] = obj[key]
    end
    obj[key] = fields[key] * factor
end

local function restore_group(group_name)
    local group = groups[group_name]
    if not group then
        return
    end
    for obj, fields in pairs(group) do
        if type(obj) == "table" then
            for key, value in pairs(fields) do
                obj[key] = value
            end
        end
    end
    groups[group_name] = setmetatable({}, {__mode = "k"})
end

local function restore_all_groups()
    restore_group("god")
    restore_group("hero_cd")
    restore_group("damage")
    restore_group("tower_speed")
    restore_group("tower_range")
    restore_group("barrack_count")
    restore_group("barrack_respawn")
end

local function deep_copy(value, seen)
    if type(value) ~= "table" then
        return value
    end
    seen = seen or {}
    if seen[value] then
        return seen[value]
    end
    local result = {}
    seen[value] = result
    for key, item in pairs(value) do
        result[deep_copy(key, seen)] = deep_copy(item, seen)
    end
    return result
end

local function clamp_number(value, minimum, maximum, fallback)
    value = tonumber(value)
    if not value then
        return fallback
    end
    if value < minimum then
        value = minimum
    elseif value > maximum then
        value = maximum
    end
    return value
end

-- 当前活跃的游戏场景对象
local function get_game()
    if type(main) == "table" and type(main.handler) == "table" then
        local item = main.handler.active_item
        if type(item) == "table" then
            if item.item_name == "game" and type(item.store) == "table" then
                return item
            end
            -- 兜底：场景名不同但确实持有关卡状态对象时同样认作游戏场景
            local store = item.store
            if type(store) == "table" then
                if store.player_gold ~= nil or store.entities ~= nil or store.waves_finished ~= nil then
                    return item
                end
            end
            return nil
        end
    end
    if type(game) == "table" and type(game.store) == "table" then
        return game
    end
    return nil
end

local function active_store()
    if type(current_store) == "table" then
        return current_store
    end
    local g = get_game()
    if g and type(g.store) == "table" then
        return g.store
    end
    return nil
end

-- 杀敌金币倍率：监听游戏自带的 got-enemy-gold，在官方发放之后补差额
local kill_gold_hook_ok = false

local function ensure_kill_gold_hook()
    if kill_gold_hook_ok then
        return
    end
    if not ok_signal or type(signal) ~= "table" or type(signal.register) ~= "function" then
        return
    end
    local handler = function(_entity, amount)
        if not config.active or not config.kill_gold_enabled then
            return
        end
        local factor = tonumber(config.kill_gold_multiplier)
        if not factor or factor <= 1 then
            return
        end
        local base = tonumber(amount) or 0
        if base <= 0 then
            return
        end
        local store = active_store()
        if type(store) ~= "table" or type(store.player_gold) ~= "number" then
            return
        end
        local bonus = math.floor(base * (factor - 1) + 0.5)
        if bonus > 0 then
            store.player_gold = store.player_gold + bonus
        end
    end
    kill_gold_hook_ok = pcall(signal.register, "got-enemy-gold", handler)
end

-- 状态文件读取：先读 save 目录（love.filesystem），再退回游戏目录（原生 io）
local function read_state()
    if not (love and love.filesystem and love.filesystem.load) then
        return nil, "love.filesystem unavailable"
    end
    local loader, err = love.filesystem.load(STATE_FILE)
    if not loader then
        return nil, err
    end
    local ok, result = pcall(loader)
    if not ok or type(result) ~= "table" then
        return nil, result
    end
    return result, nil
end

local function read_state_fallback()
    if not io or not io.open then
        return nil, "io unavailable"
    end
    local candidates = {
        love and love.filesystem and love.filesystem.getSourceBaseDirectory and
            love.filesystem.getSourceBaseDirectory() or "",
        "."
    }
    for _, base in ipairs(candidates) do
        local sep = (base:sub(-1) == "\\" or base:sub(-1) == "/") and "" or "\\"
        local path = base .. sep .. STATE_FILE
        local handle = io.open(path, "rb")
        if handle then
            local chunk = handle:read("*a")
            handle:close()
            if type(chunk) == "string" and chunk ~= "" then
                local loader, err = loadstring(chunk, "@" .. path)
                if loader then
                    local ok, result = pcall(loader)
                    if ok and type(result) == "table" then
                        return result, nil
                    end
                else
                    return nil, err
                end
            end
        end
    end
    return nil, "state file not found in source directory"
end

local function read_state_any()
    local result, err = read_state()
    if result then
        return result, nil
    end
    local alt, alt_err = read_state_fallback()
    if alt then
        return alt, nil
    end
    return nil, err or alt_err
end

local function refresh_config()
    local incoming = read_state_any()
    if type(incoming) ~= "table" then
        -- 读到半截文件时不立刻停用，等下一次轮询；只有心跳真正超时才恢复
        if os.time() - (tonumber(config.heartbeat) or 0) > HEARTBEAT_TIMEOUT then
            config.active = false
        end
        return
    end
    for key, value in pairs(incoming) do
        config[key] = value
    end
    local heartbeat = tonumber(config.heartbeat) or 0
    if os.time() - heartbeat > HEARTBEAT_TIMEOUT then
        config.active = false
    end
end

-- 速度：游戏自带 DBG_TIME_MULT（all/game.lua 的 time warp 调试通道）
local function speed_targets(g)
    local targets = {}
    if type(g) == "table" then
        targets[#targets + 1] = g
    end
    if type(game) == "table" and game ~= g then
        targets[#targets + 1] = game
    end
    return targets
end

local function restore_speed()
    if speed_original then
        for _, pair in ipairs(speed_original) do
            local owner, value = pair[1], pair[2]
            if type(owner) == "table" then
                owner.DBG_TIME_MULT = value
            end
        end
    end
    speed_owner = nil
    speed_original = nil
    speed_fraction_acc = 0
end

local function apply_speed(g)
    if not config.active or not config.speed_enabled then
        restore_speed()
        return
    end
    local targets = speed_targets(g)
    if speed_owner ~= g then
        restore_speed()
        speed_owner = g
        speed_original = {}
        for _, owner in ipairs(targets) do
            speed_original[#speed_original + 1] = { owner, owner.DBG_TIME_MULT }
        end
    end
    local requested = clamp_number(config.speed, 1, 16, 1)
    local whole = math.floor(requested)
    local fraction = requested - whole
    -- 小数倍率用相邻整数帧交替，取长期平均速度
    speed_fraction_acc = speed_fraction_acc + fraction
    if speed_fraction_acc >= 1 then
        whole = whole + 1
        speed_fraction_acc = speed_fraction_acc - 1
    end
    for _, owner in ipairs(targets) do
        owner.DBG_TIME_MULT = math.max(1, whole)
    end
end

local function restore_pause()
    if pause_owner and type(pause_owner) == "table" then
        if pause_original == NIL then
            pause_owner.paused = nil
        else
            pause_owner.paused = pause_original
        end
    end
    pause_owner = nil
    pause_original = NIL
end

local function apply_pause(store)
    if not config.active or not config.paused then
        restore_pause()
        return
    end
    if pause_owner ~= store then
        restore_pause()
        pause_owner = store
        if store.paused == nil then
            pause_original = NIL
        else
            pause_original = store.paused
        end
    end
    store.paused = true
end

local function restore_scalar(owner, original, field)
    if owner and type(owner) == "table" then
        if original == NIL then
            owner[field] = nil
        else
            owner[field] = original
        end
    end
end

local function apply_gold(store)
    if not config.active or not config.gold_enabled then
        restore_scalar(gold_owner, gold_original, "player_gold")
        gold_owner = nil
        gold_original = NIL
        return
    end
    if gold_owner ~= store then
        restore_scalar(gold_owner, gold_original, "player_gold")
        gold_owner = store
        gold_original = store.player_gold == nil and NIL or store.player_gold
    end
    store.player_gold = math.floor(clamp_number(config.gold_value, 0, 999999999, 99999))
end

local function apply_lives(store)
    if not config.active or not config.lives_enabled then
        restore_scalar(lives_owner, lives_original, "lives")
        lives_owner = nil
        lives_original = NIL
        return
    end
    if lives_owner ~= store then
        restore_scalar(lives_owner, lives_original, "lives")
        lives_owner = store
        lives_original = store.lives == nil and NIL or store.lives
    end
    store.lives = math.floor(clamp_number(config.lives_value, 1, 9999, 20))
end

-- 递归缩放 cooldown 字段（英雄技能 / 防御塔攻速共用）
local function recursive_scale_cooldowns(group_name, value, divisor, depth, visited)
    if type(value) ~= "table" or depth < 0 or visited[value] then
        return
    end
    visited[value] = true
    for key, item in pairs(value) do
        if type(item) == "table" then
            recursive_scale_cooldowns(group_name, item, divisor, depth - 1, visited)
        elseif type(item) == "number" and (key == "cooldown" or string.match(tostring(key), "_cooldown$")) then
            local group = weak_group(group_name)
            local fields = group[value]
            if not fields then
                fields = {}
                group[value] = fields
            end
            if fields[key] == nil then
                fields[key] = item
            end
            value[key] = divisor == 0 and 0 or fields[key] / divisor
        end
    end
end

local function set_range_fields(entity, multiplier)
    local function scale(obj, key)
        if type(obj) == "table" and type(obj[key]) == "number" then
            scale_field("tower_range", obj, key, multiplier)
        end
    end
    scale(entity.attacks, "range")
    scale(entity.melee, "range")
    scale(entity.ranged, "range")
    scale(entity.ranged, "range_while_blocking")
    local lists = {
        entity.attacks and entity.attacks.list,
        entity.melee and entity.melee.attacks,
        entity.ranged and entity.ranged.attacks
    }
    for _, list in ipairs(lists) do
        if type(list) == "table" then
            for _, attack in pairs(list) do
                scale(attack, "max_range")
                scale(attack, "min_range")
                scale(attack, "range")
            end
        end
    end
end

local function set_damage_fields(entity, multiplier)
    local function scale(obj, key)
        if type(obj) == "table" and type(obj[key]) == "number" then
            scale_field("damage", obj, key, multiplier)
        end
    end
    scale(entity.tower, "damage_factor")
    scale(entity.unit, "damage_factor")
    local lists = {
        entity.attacks and entity.attacks.list,
        entity.melee and entity.melee.attacks,
        entity.ranged and entity.ranged.attacks
    }
    for _, list in ipairs(lists) do
        if type(list) == "table" then
            for _, attack in pairs(list) do
                scale(attack, "damage_min")
                scale(attack, "damage_max")
            end
        end
    end
end

local function apply_entities(store)
    if type(store.entities) ~= "table" then
        -- 恢复放在判断之外，避免主菜单 / 关卡加载途中切开关后改动残留
        restore_group("god")
        restore_group("hero_cd")
        restore_group("damage")
        restore_group("tower_speed")
        restore_group("tower_range")
        return
    end
    local invincible = config.active and config.invincible
    local no_cooldown = config.active and config.hero_no_cooldown
    local damage_enabled = config.active and config.damage_enabled
    local tower_speed_enabled = config.active and config.tower_speed_enabled
    local tower_range_enabled = config.active and config.tower_range_enabled
    local damage_multiplier = clamp_number(config.damage_multiplier, 0.1, 100, 3)
    local speed_multiplier = clamp_number(config.tower_speed_multiplier, 0.1, 50, 3)
    local range_multiplier = clamp_number(config.tower_range_multiplier, 0.1, 20, 2)

    for _, entity in pairs(store.entities) do
        if type(entity) == "table" then
            local friendly = entity.tower or entity.hero or entity.soldier
            if invincible and friendly and entity.health and not entity.health.dead
                and type(entity.health.hp_max) == "number" then
                remember_and_set("god", entity.health, "hp", entity.health.hp_max)
            end
            if no_cooldown and entity.hero then
                recursive_scale_cooldowns("hero_cd", entity, 0, 5, {})
            end
            if tower_speed_enabled and entity.tower then
                recursive_scale_cooldowns("tower_speed", entity, speed_multiplier, 4, {})
            end
            if tower_range_enabled and entity.tower then
                set_range_fields(entity, range_multiplier)
            end
            if damage_enabled and friendly then
                set_damage_fields(entity, damage_multiplier)
            end
        end
    end

    if not invincible then restore_group("god") end
    if not no_cooldown then restore_group("hero_cd") end
    if not damage_enabled then restore_group("damage") end
    if not tower_speed_enabled then restore_group("tower_speed") end
    if not tower_range_enabled then restore_group("tower_range") end
end

-- 兵营：出怪人数 entity.barrack.max_soldiers（原版 3）
--       补员时间 entity.health.dead_lifetime
local function apply_barracks(store)
    if not config.active or not config.barrack_enabled or type(store.entities) ~= "table" then
        restore_group("barrack_count")
        restore_group("barrack_respawn")
        return
    end
    local count = math.floor(clamp_number(config.barrack_soldiers, 1, 10, 3))
    local scale = clamp_number(config.barrack_respawn_scale, 0.1, 5.0, 1.0)
    for _, entity in pairs(store.entities) do
        if type(entity) == "table" and type(entity.barrack) == "table" and entity.tower then
            remember_and_set("barrack_count", entity.barrack, "max_soldiers", count)
            local soldiers = entity.barrack.soldiers
            if type(soldiers) == "table" then
                for _, s in pairs(soldiers) do
                    if type(s) == "table" and type(s.health) == "table" then
                        scale_field("barrack_respawn", s.health, "dead_lifetime", scale)
                    end
                end
            end
        end
    end
end

-- 只读模式：完全不触碰存档文件
local function read_only_active()
    return config.active and config.read_only == true
end

local function restore_slot_snapshot()
    if read_only_active() then
        slot_snapshot = nil
        slot_snapshot_idx = nil
        return
    end
    if not slot_snapshot or not ok_storage then
        slot_snapshot = nil
        slot_snapshot_idx = nil
        return
    end
    local ok, slot = pcall(function()
        return storage:load_slot(slot_snapshot_idx)
    end)
    if ok and type(slot) == "table" then
        slot.gems = slot_snapshot.gems
        slot.levels = deep_copy(slot_snapshot.levels or {})
        pcall(function()
            storage:save_slot(slot, slot_snapshot_idx, true)
        end)
    end
    slot_snapshot = nil
    slot_snapshot_idx = nil
end

-- 关卡总数：优先读 game_settings.last_level，回退到 KR3 主线 14 关
local function last_level_index()
    if ok_gs and type(GS) == "table" then
        local n = tonumber(GS.last_level)
        if n and n >= 1 then
            return math.floor(n)
        end
    end
    return FALLBACK_LAST_LEVEL
end

-- 存档写入：改动后节流落盘，避免每帧写文件；只读模式下永不触发
local slot_dirty = false
local last_slot_save = 0

local function flush_slot(idx)
    if read_only_active() or not ok_storage or not slot_dirty then
        return
    end
    if os.time() - last_slot_save < 2 then
        return
    end
    last_slot_save = os.time()
    slot_dirty = false
    local ok, slot = pcall(function()
        return storage:load_slot(idx)
    end)
    if ok and type(slot) == "table" then
        pcall(function()
            storage:save_slot(slot, idx, true)
        end)
    end
end

local function apply_slot_features()
    if read_only_active() then
        if slot_snapshot then
            slot_snapshot = nil
            slot_snapshot_idx = nil
        end
        return
    end
    local needs_slot = config.active and (config.gems_enabled or config.unlock_levels or config.three_stars)
    if not needs_slot or not ok_storage or not storage.active_slot_idx then
        if slot_snapshot and not needs_slot then
            restore_slot_snapshot()
        end
        return
    end
    local idx = storage.active_slot_idx
    if slot_snapshot and slot_snapshot_idx ~= idx then
        restore_slot_snapshot()
    end
    local ok, slot = pcall(function()
        return storage:load_slot(idx)
    end)
    if not ok or type(slot) ~= "table" then
        return
    end
    if not slot_snapshot then
        slot_snapshot_idx = idx
        slot_snapshot = {
            gems = slot.gems,
            levels = deep_copy(slot.levels or {})
        }
    end
    if config.gems_enabled then
        slot.gems = math.floor(clamp_number(config.gems_value, 0, 999999999, 99999))
    end
    if config.unlock_levels or config.three_stars then
        slot.levels = slot.levels or {}
        local last_level = last_level_index()
        for i = 1, last_level do
            if type(slot.levels[i]) ~= "table" then
                slot.levels[i] = {}
            end
            if config.three_stars then
                slot.levels[i].stars = 3
            end
        end
    end
    slot_dirty = true
    flush_slot(idx)
end

local function command_skip_wave(store)
    local nonce = tonumber(config.cmd_skip_wave) or 0
    if nonce > last_skip_wave then
        last_skip_wave = nonce
        store.send_next_wave = true
    end
end

local function command_force_wave(store)
    local nonce = tonumber(config.cmd_force_wave) or 0
    if nonce > last_force_wave then
        last_force_wave = nonce
        store.force_next_wave = true
    end
end

-- 立即通关
--
-- 官方胜利流程（all/systems.lua 关卡协程结尾）：
--     run_complete -> 写 slot_level/levels[idx].stars/already_won -> save_slot
--                  -> emit "game-victory" -> emit "game-victory-after"
-- 解锁下一关是协程正常跑到结尾的副作用。
-- 因此这里只制造官方判定所需的条件（无存活敌人 + 波次已打完），
-- 分两帧推进，由 systems.lua 自己判定胜利，全程不构造 game_outcome、不补发胜利信号。
local win_stage = nil
local win_store = nil

local function kill_all_enemies(store)
    if type(store.entities) ~= "table" then
        return
    end
    for _, entity in pairs(store.entities) do
        if type(entity) == "table" and entity.enemy and type(entity.health) == "table" then
            if not entity.health.dead then
                entity.health.hp = 0
                entity.health.dead = true
            end
        end
    end
end

local function mark_waves_finished(store)
    if type(store.waves_finished) == "number" then
        store.waves_finished = store.waves_finished + 1
    end
    store.send_next_wave = true
    store.force_next_wave = true
end

local function command_instant_win(store)
    local nonce = tonumber(config.cmd_win) or 0
    if nonce <= last_win then
        return
    end
    last_win = nonce
    if store.game_outcome then
        return
    end
    win_stage = 0
    win_store = store
end

local function process_instant_win(store)
    if not config.active or store.game_outcome then
        win_stage = nil
        win_store = nil
        return
    end
    if win_store ~= store then
        if win_store ~= nil then
            win_stage = nil
        end
        win_store = store
        return
    end
    if win_stage == nil then
        return
    end
    if win_stage == 0 then
        kill_all_enemies(store)
        win_stage = 1
    elseif win_stage == 1 then
        mark_waves_finished(store)
        win_stage = 2
    else
        if not store.game_outcome then
            kill_all_enemies(store)
            store.force_next_wave = true
        else
            win_stage = nil
            win_store = nil
        end
    end
end

local function restore_runtime()
    restore_speed()
    restore_pause()
    restore_scalar(gold_owner, gold_original, "player_gold")
    gold_owner = nil
    gold_original = NIL
    restore_scalar(lives_owner, lives_original, "lives")
    lives_owner = nil
    lives_original = NIL
    restore_all_groups()
end

-- 游戏内热键：桥接只做按键识别，把动作回传 GUI，由 GUI 改状态再下发，
-- 保证状态文件只有 GUI 一个写入方，不会互相覆盖。
local hotkey_seq = 0
local hotkey_pending = ""

local function emit_hotkey(action)
    hotkey_seq = hotkey_seq + 1
    hotkey_pending = tostring(hotkey_seq) .. ":" .. action
end

-- ==== 调试探针（仅测试用，GUI 不暴露）====
local debug_result = ""
local probe_item, probe_text = nil, ""
local probe_fields_item, probe_fields_text = nil, ""
local probe_handler, probe_handler_text = nil, ""
local last_debug_nonce = nil

local function collect_names(target, out)
    if type(target) ~= "table" then
        return
    end
    for k, v in pairs(target) do
        if type(k) == "string" and type(v) == "function" then
            out[k] = true
        end
    end
end

local function probe_active_item()
    local item = main and main.handler and main.handler.active_item
    if type(item) ~= "table" then
        return ""
    end
    if probe_item == item then
        return probe_text
    end
    local out = {}
    collect_names(item, out)
    local mt = getmetatable(item)
    local guard = 0
    while type(mt) == "table" and guard < 5 do
        collect_names(mt.__index, out)
        collect_names(mt, out)
        mt = getmetatable(mt)
        guard = guard + 1
    end
    local list = {}
    for k in pairs(out) do
        list[#list + 1] = k
    end
    table.sort(list)
    probe_item = item
    probe_text = table.concat(list, ",")
    return probe_text
end

local function probe_fields()
    local item = main and main.handler and main.handler.active_item
    if type(item) ~= "table" then
        return ""
    end
    if probe_fields_item == item then
        return probe_fields_text
    end
    local list = {}
    for k, v in pairs(item) do
        if type(k) == "string" and type(v) ~= "function" then
            local extra = ""
            if type(v) == "table" then
                local n = 0
                for _ in pairs(v) do n = n + 1 end
                extra = "#" .. tostring(n)
            elseif type(v) ~= "nil" then
                extra = "=" .. tostring(v)
                if #extra > 24 then extra = extra:sub(1, 24) end
            end
            list[#list + 1] = k .. extra
        end
    end
    table.sort(list)
    probe_fields_item = item
    probe_fields_text = table.concat(list, ",")
    return probe_fields_text
end

local function probe_handler_methods()
    local handler = main and main.handler
    if type(handler) ~= "table" then
        return ""
    end
    if probe_handler == handler then
        return probe_handler_text
    end
    local out = {}
    collect_names(handler, out)
    local mt = getmetatable(handler)
    local guard = 0
    while type(mt) == "table" and guard < 5 do
        collect_names(mt.__index, out)
        collect_names(mt, out)
        mt = getmetatable(mt)
        guard = guard + 1
    end
    local list = {}
    for k in pairs(out) do
        list[#list + 1] = k
    end
    table.sort(list)
    probe_handler = handler
    probe_handler_text = table.concat(list, ",")
    return probe_handler_text
end


local function resolve_path(path)
    local cur = main and main.handler and main.handler.active_item
    for part in tostring(path):gmatch("[%w_]+") do
        if type(cur) ~= "table" then
            return nil
        end
        local num = tonumber(part)
        if num then
            cur = cur[num]
        else
            cur = cur[part]
            if cur == nil then
                local mt = getmetatable(cur)
                if type(mt) == "table" and type(mt.__index) == "table" then
                    cur = mt.__index[part]
                end
            end
        end
    end
    return cur
end

local function describe_value(value, depth)
    depth = depth or 0
    local t = type(value)
    if t ~= "table" then
        local text = tostring(value)
        if #text > 60 then
            text = text:sub(1, 60)
        end
        return t .. ":" .. text
    end
    local keys = {}
    for k, v in pairs(value) do
        if type(v) ~= "function" then
            local kt = type(k)
            local label
            if kt == "string" then
                label = k
            elseif kt == "number" then
                label = "[" .. tostring(k) .. "]"
            else
                label = "<" .. kt .. ">"
            end
            if type(v) == "table" then
                keys[#keys + 1] = label .. "{tbl}"
            else
                keys[#keys + 1] = label .. "=" .. tostring(v)
            end
        end
    end
    table.sort(keys)
    local text = "table{" .. table.concat(keys, " ") .. "}"
    if #text > 600 then
        text = text:sub(1, 600) .. "..."
    end
    return text
end

local function process_debug_call()
    local name = config.debug_call
    if name == "getfield" then
        local ok, value = pcall(resolve_path, config.debug_args)
        debug_result = ok and describe_value(value) or ("err:" .. tostring(value))
        return
    end
    if type(name) ~= "string" or name == "" then
        return
    end
    local nonce = tostring(config.debug_nonce or "")
    if nonce ~= "" and nonce == last_debug_nonce then
        return
    end
    last_debug_nonce = nonce
    local is_handler = name:sub(1, 8) == "handler."
    local target
    if is_handler then
        target = main and main.handler
        name = name:sub(9)
    else
        target = main and main.handler and main.handler.active_item
    end
    local fn = (type(target) == "table") and target[name] or nil
    if type(fn) ~= "function" then
        debug_result = "no_method:" .. name
        return
    end
    local args = {}
    local raw = config.debug_args
    if type(raw) == "string" and raw ~= "" then
        for part in raw:gmatch("[^,]+") do
            local num = tonumber(part)
            args[#args + 1] = num or part
        end
    end
    local ok, err = pcall(fn, target, unpack(args))
    debug_result = ok and ("ok:" .. name) or ("err:" .. name .. ":" .. tostring(err))
end

local function mouse_text()
    local ok, x, y = pcall(function()
        local mx, my = love.mouse.getPosition()
        return mx, my
    end)
    if ok and type(x) == "number" and type(y) == "number" then
        return math.floor(x) .. "," .. math.floor(y)
    end
    return "-1,-1"
end

local function mode_text()
    local ok, w, h, flags = pcall(function()
        local mw, mh, mf = love.window.getMode()
        return mw, mh, mf
    end)
    if ok and type(w) == "number" then
        local fs = (type(flags) == "table" and flags.fullscreen) and "1" or "0"
        return tostring(w) .. "x" .. tostring(h) .. ",fs" .. fs
    end
    return "?"
end

local function write_status(g)
    local scene = g and "game" or "menu"
    local slot_idx = ok_storage and storage.active_slot_idx or 0
    local level_idx = 0
    local level_name = ""
    local gold = ""
    local lives = ""
    local entities = 0
    if g and type(g.store) == "table" then
        local store = g.store
        if type(store.level_idx) == "number" then
            level_idx = store.level_idx
        end
        if type(store.level_name) == "string" then
            level_name = store.level_name
        end
        if type(store.player_gold) == "number" then
            gold = tostring(math.floor(store.player_gold))
        end
        if type(store.lives) == "number" then
            lives = tostring(math.floor(store.lives))
        end
        if type(store.entities) == "table" then
            local n = 0
            for _ in pairs(store.entities) do n = n + 1 end
            entities = n
        end
    end
    local lines = {
        "bridge=1",
        "mouse=" .. mouse_text(),
        "mode=" .. mode_text(),
        "bridge_version=" .. BRIDGE_VERSION,
        "active=" .. (config.active and "1" or "0"),
        "scene=" .. scene,
        "slot=" .. tostring(slot_idx or 0),
        "level=" .. tostring(level_idx),
        "level_name=" .. level_name,
        "gold=" .. gold,
        "lives=" .. lives,
        "entities=" .. tostring(entities),
        "revision=" .. tostring(config.revision or 0),
        "restored=" .. (config.active and "0" or "1"),
        "kill_gold_hook=" .. (kill_gold_hook_ok and "1" or "0"),
        "barrack=" .. (config.barrack_enabled and "1" or "0"),
        "read_only=" .. (config.read_only and "1" or "0"),
        "slot_writable=" .. ((read_only_active()) and "0" or "1"),
        "speed=" .. tostring(clamp_number(config.speed, 1, 16, 1)),
        "last_level=" .. tostring(last_level_index()),
        "game_settings=" .. (ok_gs and "1" or "0"),
        "storage=" .. (ok_storage and "1" or "0"),
        "signal=" .. (ok_signal and "1" or "0"),
        "win_stage=" .. tostring(win_stage or -1),
        "hotkey=" .. hotkey_pending,
        "item_name=" .. (function()
            local ok, name = pcall(function()
                return main.handler.active_item and tostring(main.handler.active_item.item_name or "")
            end)
            if ok and name then return name end
            return ""
        end)(),
        "focus=" .. (function()
            local ok, f = pcall(function()
                return love.window and love.window.hasFocus and love.window.hasFocus() and "1" or "0"
            end)
            if ok and f then return f end
            return "?"
        end)(),
        "game_outcome=" .. ((g and g.store and g.store.game_outcome) and "1" or "0"),
        "debug_methods=" .. probe_active_item(),
        "debug_fields=" .. probe_fields(),
        "debug_handler=" .. probe_handler_methods(),
        "debug_result=" .. debug_result,
        "version_string=" .. "kr3-desktop-6.4.46",
        "timestamp=" .. tostring(os.time())
    }
    local payload = table.concat(lines, "\n") .. "\n"
    pcall(function()
        love.filesystem.write(STATUS_FILE, payload)
    end)
    pcall(function()
        if not io or not io.open then
            return
        end
        local base = love.filesystem.getSourceBaseDirectory and
            love.filesystem.getSourceBaseDirectory() or "."
        local sep = (base:sub(-1) == "\\" or base:sub(-1) == "/") and "" or "\\"
        local handle = io.open(base .. sep .. STATUS_FILE, "wb")
        if handle then
            handle:write(payload)
            handle:close()
        end
    end)
end

function M:init()
    refresh_config()
    ensure_kill_gold_hook()
    write_status(get_game())
end

function M:game_init()
    current_store = nil
    gold_owner = nil
    gold_original = NIL
    lives_owner = nil
    lives_original = NIL
    pause_owner = nil
    pause_original = NIL
    win_stage = nil
    win_store = nil
    restore_all_groups()
end

function M:update(dt)
    dt = tonumber(dt) or 0
    poll_acc = poll_acc + dt
    status_acc = status_acc + dt

    if poll_acc >= STATE_POLL_INTERVAL then
        poll_acc = 0
        refresh_config()
        process_debug_call()
    end

    local g = get_game()
    if not config.active then
        restore_runtime()
        current_store = nil
    else
        apply_slot_features()
        if g and g.store then
            local store = g.store
            if current_store ~= store then
                restore_pause()
                restore_scalar(gold_owner, gold_original, "player_gold")
                restore_scalar(lives_owner, lives_original, "lives")
                restore_all_groups()
                current_store = store
                gold_owner = nil
                gold_original = NIL
                lives_owner = nil
                lives_original = NIL
                win_stage = nil
                win_store = nil
            end
            apply_speed(g)
            apply_pause(store)
            apply_gold(store)
            apply_lives(store)
            apply_entities(store)
            apply_barracks(store)
            command_skip_wave(store)
            command_force_wave(store)
            command_instant_win(store)
            process_instant_win(store)
        else
            restore_speed()
            restore_pause()
            restore_scalar(gold_owner, gold_original, "player_gold")
            gold_owner = nil
            gold_original = NIL
            restore_scalar(lives_owner, lives_original, "lives")
            lives_owner = nil
            lives_original = NIL
            restore_all_groups()
            current_store = nil
            win_stage = nil
            win_store = nil
        end
    end

    if status_acc >= STATUS_INTERVAL then
        status_acc = 0
        write_status(g)
    end
end

function M:keypressed(key, isrepeat)
    -- 桥接不直接改配置（状态文件的唯一写入方是 GUI），只把动作回传
    if isrepeat then
        return
    end
    if not (love and love.keyboard and love.keyboard.isDown) then
        return
    end
    local ok, ctrl_down = pcall(love.keyboard.isDown, "lctrl", "rctrl")
    local ok2, alt_down = pcall(love.keyboard.isDown, "lalt", "ralt")
    if not (ok and ctrl_down and ok2 and alt_down) then
        return
    end
    if key == "1" or key == "kp1" then
        emit_hotkey("toggle_gold")
    elseif key == "2" or key == "kp2" then
        emit_hotkey("toggle_lives")
    elseif key == "3" or key == "kp3" then
        emit_hotkey("toggle_invincible")
    elseif key == "4" or key == "kp4" then
        emit_hotkey("cycle_speed")
    elseif key == "5" or key == "kp5" then
        emit_hotkey("toggle_pause")
    end
end

return M
