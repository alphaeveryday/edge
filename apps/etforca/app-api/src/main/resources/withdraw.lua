local old = redis.call('HGET', KEYS[1], ARGV[1])
if not old then
    return 0
end
redis.call('HDEL', KEYS[1], ARGV[1])
return redis.call('HINCRBY', KEYS[2], old, -1)
