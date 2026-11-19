"""Exercise actual lock/store invariants without sockets or generated keys."""
import json,threading,unittest
from reference import Store

class ReferenceProtocol(unittest.TestCase):
    def test_only_one_distinct_concurrent_owner_can_acquire(self):
        store=Store();barrier=threading.Barrier(8);results=[]
        def attempt(i):barrier.wait();results.append(store.acquire({'ID':str(i)})[0])
        threads=[threading.Thread(target=attempt,args=(i,)) for i in range(8)]
        for thread in threads:thread.start()
        for thread in threads:thread.join()
        self.assertEqual(results.count(200),1);self.assertEqual(results.count(423),7)
    def test_wrong_owner_cannot_write_or_release(self):
        store=Store();store.acquire({'ID':'owner'})
        self.assertEqual(store.release({'ID':'wrong'})[0],409)
        self.assertEqual(store.write('wrong',b'{"encrypted_data":"fixture"}')[0],409)
        self.assertEqual(store.lock['ID'],'owner');self.assertIsNone(store.state)
    def test_plaintext_is_rejected_and_encrypted_generations_are_retained(self):
        store=Store();store.acquire({'ID':'owner'})
        self.assertEqual(store.write('owner',b'{"resources":[]}')[0],400)
        for value in ('first','second'):self.assertEqual(store.write('owner',json.dumps({'encrypted_data':value}).encode())[0],200)
        self.assertEqual(len(store.history),2);self.assertNotEqual(store.history[0]['sha256'],store.history[1]['sha256'])
        self.assertEqual(store.release({'ID':'owner'})[0],200);self.assertEqual(store.acquire({'ID':'new'})[0],200)

if __name__=='__main__':unittest.main()
