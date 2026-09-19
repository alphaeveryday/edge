local old = redis.call('HGET', KEYS[1], ARGV[1])
if old == ARGV[2] then
    return 0
end
redis.call('HSET', KEYS[1], ARGV[1], ARGV[2])
if old then
    redis.call('HINCRBY', KEYS[2], old, -1)
end
redis.call('HINCRBY', KEYS[2], ARGV[2], 1)
redis.call('HSET', KEYS[3], ARGV[1], ARGV[2])
redis.call('SADD', KEYS[4], ARGV[3])
return 1
