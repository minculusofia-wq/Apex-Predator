import asyncio
import sys
import os
sys.path.insert(0, os.getcwd())

from core.auto_optimizer import AutoOptimizer, OptimizerMode
from core.gabagool import GabagoolEngine, GabagoolConfig

async def test_optimizer_crash():
    print("🚀 Starting Crash Test...")
    
    # Init Gabagool
    print("1. Initializing Gabagool...")
    config = GabagoolConfig()  # Should have min_improvement now
    gabagool = GabagoolEngine(config=config)
    print(f"   Config keys: {config.__dict__.keys()}")
    
    # Init Optimizer
    print("2. Initializing AutoOptimizer...")
    optimizer = AutoOptimizer(scanner=None, gabagool=gabagool, mode=OptimizerMode.FULL_AUTO)
    
    # Test get_status
    print("3. Testing get_status()...")
    try:
        status = optimizer.get_status()
        print("   ✅ get_status() Success!")
        print(f"   Status keys: {status.keys()}")
    except Exception as e:
        print(f"   ❌ get_status() CRASHED: {e}")
        import traceback
        traceback.print_exc()

    # Test get_suggestions
    print("4. Testing get_suggestions()...")
    try:
        suggestions = optimizer.get_suggestions()
        print("   ✅ get_suggestions() Success!")
    except Exception as e:
        print(f"   ❌ get_suggestions() CRASHED: {e}")
        import traceback
        traceback.print_exc()
        
    print("🏁 Test Complete")

if __name__ == "__main__":
    asyncio.run(test_optimizer_crash())
