'''
    Copyright (C) 2018 Gitcoin Core

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU Affero General Public License as published
    by the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
    GNU Affero General Public License for more details.

    You should have received a copy of the GNU Affero General Public License
    along with this program. If not, see <http://www.gnu.org/licenses/>.

'''

import logging
import time
import requests
import json

from django.core.management.base import BaseCommand
from django.conf import settings

from dashboard.utils import get_web3
from kudos.utils import KudosContract
import web3
import warnings
warnings.simplefilter("ignore", category=DeprecationWarning)
warnings.simplefilter("ignore", category=UserWarning)

logging.getLogger("requests").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("web3").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


class Command(BaseCommand):
    help = 'listens for kudos token changes '

    def add_arguments(self, parser):
        parser.add_argument('-n', '--network', default='localhost', type=str)
        parser.add_argument('-m', '--syncmethod', default='block', type=str, choices=['filter', 'block', 'opensea'])
        parser.add_argument('-i', '--interval', default=1, type=int)

    def opensea_listener(self, kudos_contract, interval):
        if kudos_contract.network == 'rinkeby':
            url = 'https://rinkeby-api.opensea.io/api/v1/events'
        elif kudos_contract.network == 'mainnet':
            url = 'https://api.opensea.io/api/v1/events'
        else:
            raise RuntimeError('The Open Sea API is only supported for contracts on rinkeby and mainnet.')

        # Event API
        payload = dict(
            asset_contract_address=kudos_contract.address,
            event_type='transfer',
            )
        headers = {'X-API-KEY': settings.OPENSEA_API_KEY}
        asset_token_id = 0
        transaction_hash = 0
        while True:
            cache = (asset_token_id, transaction_hash)
            r = requests.get(url, params=payload, headers=headers)
            r.raise_for_status()

            asset_token_id = r.json()['asset_events'][0]['asset']['token_id']
            transaction_hash = r.json()['asset_events'][0]['transaction']['transaction_hash']
            # If the result is the same as last time, don't re-sync the database
            if cache == (asset_token_id, transaction_hash):
                continue
            logger.info(f'token_id: {asset_token_id}, txid: {transaction_hash}')
            kudos_contract.sync_db(kudos_id=int(asset_token_id), txid=transaction_hash)
            time.sleep(interval)

    def filter_listener(self, kudos_contract, interval):

        event_filter = kudos_contract._contract.events.Transfer.createFilter(fromBlock='latest')
        # params = dict(
        #     fromBlock='latest',
        #     toBlock='latest',
        #     address=kudos_contract.address,
        #     topics=['034ac9c3d6ddb432341e5fdbaba91bb6a01a6aab04b202888634e16a7c6656b2']
        #     )
        # event_filter = kudos_contract._w3sockets.eth.filter({"address": kudos_contract.address})

        while True:
            for event in event_filter.get_new_entries():
                msg = dict(blockNumber=event.blockNumber,
                           _tokenId=event.args._tokenId,
                           transactionHash=event.transactionHash.hex()
                           )
                logger.info(f'Transfer event:  {msg}')
                if logger.isEnabledFor(logging.DEBUG):
                    logger.debug(f'Raw Transfer event: {event}')
                kudos_contract._w3.eth.waitForTransactionReceipt(msg['transactionHash'])
                logger.debug(f"Tx hash: {msg['transactionHash']}")
                kudos_contract.sync_db(kudos_id=msg['_tokenId'], txid=msg['transactionHash'])
            time.sleep(interval)

    def block_listener(self, kudos_contract, interval):
        block = 'latest'
        last_block_hash = None
        while True:
            # wait for a new block
            # logger.info(f'block: {block}')
            block = kudos_contract._w3.eth.getBlock('latest')
            block_hash = block['hash']
            block_number = block['number']

            # logger.info(f'last_block_hash: {last_block_hash}')
            # logger.info(f'block_hash: {block_hash}')
            if last_block_hash == block_hash:
                time.sleep(interval)
                continue

            logger.info('got new block %s' % kudos_contract._w3.toHex(block_hash))
            logger.info(f'block id: {block_number}')

            # get txs
            transactions = block['transactions']
            for tx in transactions:
                tx = kudos_contract._w3.eth.getTransaction(tx)
                if not tx or tx['to'] != kudos_contract.address:
                    continue

                logger.info('found a kudos tx')
                # logger.info(dir(tx))
                # logger.info(tx.keys())
                data = tx['input']
                method_id = data[:10]
                logger.info(f'method_id:  {method_id}')

                # Check if its a Clone or cloneAndTransfer function call
                if method_id == '0xdaa6eb1d' or method_id == '0xd319784f':
                    kudos_contract._w3.eth.waitForTransactionReceipt(tx['hash'])
                    kudos_id = kudos_contract._contract.functions.totalSupply().call()
                    if kudos_contract.network == 'localhost':
                        # On localhost, the tx syncs faster than the website loads
                        time.sleep(3)
                    kudos_contract.sync_db(kudos_id=kudos_id, txid=tx['hash'].hex())
                    # # Get the kudos_id of the newly cloned Kudos
                    # kudos_id = kudos_contract.functions.totalSupply().call()
                    # # Update the database with the newly cloned Kudos
                    # update_kudos_db(kudos_id, network)
                    # # Find the name of the Kudos that was cloned
                    # kudos = get_kudos_from_web3(kudos_id, network)
                    # kudos_map = get_kudos_map(kudos)
                    # # Find the ID of the Gen0 Kudos that was cloned
                    # gen0_id = get_gen0_id_from_web3(kudos_map['name'], network)
                    # # Update the Gen0 Kudos in the database
                    # update_kudos_db(gen0_id, network)

            last_block_hash = block_hash

    def handle(self, *args, **options):
        network = options['network']
        syncmethod = options['syncmethod']
        interval = options['interval']

        kudos_contract = KudosContract(network)

        if syncmethod == 'filter':
            kudos_contract = KudosContract(network, sockets=True)
            self.filter_listener(kudos_contract, interval)
        elif syncmethod == 'block':
            self.block_listener(kudos_contract, interval)
        elif syncmethod == 'opensea':
            self.opensea_listener(kudos_contract, interval)