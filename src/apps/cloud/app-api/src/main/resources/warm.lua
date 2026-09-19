if redis.call('EXISTS', KEYS[1]) == 1 or redis.call('HLEN', KEYS[3]) > 0 then
    return 0
end
redis.call('HSET', KEYS[2], 'BUY', ARGV[1], 'HOLD', ARGV[2], 'SELL', ARGV[3])
for i = 4, #ARGV, 2 do
    redis.call('HSET', KEYS[1], ARGV[i], ARGV[i + 1])
end
return 1
